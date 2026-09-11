# Moonraker Print Follower roadmap

This is the direction of travel, not an implementation contract. `ARCHITECTURE.md`
stays the binding description of how the code is organised today; this file says
what the releases ahead aim to deliver and why they are ordered the way they are.
Version numbers and the release checklist live in `INSTRUCTIONS.md`. Items here
are proposals — each becomes binding only when its release branch exists.

Current release: **3.6.0** (shipped 2026-09-11: the file manager,
console resize and e-stop reconnect).

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

## 3.6.0 — File manager and the small controls — SHIPPED (2026-09-11)

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
release). The author's constraints (2026-09-10): no ridiculously large
sizes — the drag range is capped — and dragging small enough snaps the
console into its collapsed state.

**Layer numbering from the file's own comments:** DEFERRED by the
author at the round-2 walk ("Sure, defer") — the flip as ruled is a
silent no-op for `one_at_a_time` files (duplicate `;LAYER:0` discards
the layer map) and off-by-one for preprocessed files (comments are
0-based and signed; Cura's GUI and Klipper are 1-based; round-2 domain
D7). It joins the 4.0.0 layer-hardening pack, gate and base ruling
included.

The last large Mainsail parity piece: browse remote gcode, print and delete.
`RemoteFileService` and its `FileLease` lifetime model already provide the
safe download path; this release adds listing UI and the print/delete
command surface on top. Print-from-file is as powerful as the console (it
IS the console path plus print start): it needs the same ready-retry,
power-device gating and state-expectation machinery as UploadController,
delete of the currently-printing file must surface Moonraker's refusal
cleanly, and starting a print must trigger the existing job-observation →
load flow (stated so the wiring is tested).

**The author's vision for the file manager (2026-09-10, verbatim):** "I think
the file manager might be better off as a popup that opens in the middle of
the screen when the user clicks a 'File manager' button somewhere in the
printer controls pane" — it should "mirror Mainsail's file manager pretty
similarly". Columns: "Thumbnail (if available), Name, Print attempts (count,
last status - success vs fail), Last modified, Object height, Layer height,
Estimated Print time, Last print time, Slicer, Extruder temp, Bed temp,
Filament usage estimate". The metadata rule (verbatim): "What's returned in
the details is contingent on whether you have to download the GCode. If it
requires downloading Gcode to be able to get this info, then don't do it,
that's way too expensive." Functions: "Delete (individually or bulk select),
Rename (individually), Print (individually), Download"; "Upload external
gcode" via a file picker ("Use the filename from the actual file"); "Upload
current slice - ask for the file name like the 'Preview' upload pane and
offer to print when uploaded." Search: "predominantly by name" — "I think
only name otherwise results could be confusing." Filters (open for panel
input): "filter by slicer, date (earlier than X, later than X), layer
height? file size? duration? I don't know, let me know what you and the
panel think". Listing (verbatim): "File listings should be paginated and
loaded lazily to avoid a huge amount of data coming down in one go. Allow
the user to pick page size, scroll move between pages (both via advance/
reverse buttons and maybe direct page selection to allow the user to skip
many pages). User should be able to sort by any of the columns, but doing
so will require downloading all the metadata to avoid sorts only working
on a single page." And: "It'd be good if we could see the free disk space
on the printer."

**Phase-0 rulings (2026-09-10, with the author):**
- The listing comes from `server/files/directory?extended=true`
  (round-1 critic: plain `server/files/list` carries no metadata, so the
  12-column table needs the extended form; the same response carries
  `disk_usage`). Round-2 change: subfolder navigation is in scope, so
  the grid lists ONE directory at a time with a breadcrumb — no
  full-tree flattening. Pagination, sorting and filtering are
  client-side ("If we have to download it all at once, so be it"). Lazy
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
  strip (new, the author's idea) LEADS the popup — the author's
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
- Column order: drag-to-reorder, persisted, default = the author's
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

**Round-1 rulings (2026-09-10, walked with the author):**
- Attempts column: the status word, colour-coded, never flattened to
  success/fail; the count carries a "within history retention" tooltip.
- Reflow: the no-reflow rule holds for the Monitor; the file-manager
  popup is exempt — the author, verbatim: "Reflowing the file manager
  is fine, there's nothing critical on that." The structural QML pin
  still sweeps the popup, so its `visible:` entries land in the allow
  list citing this ruling.
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
  three times counts once (the author's own "distinct files").

**Carried over from the 3.5.x decision log** — IN per the author
("Sure, do all 3"):
- the private persist path (`binding.persist(config)`, R5-3 Option B)
- the client-side send queue (R5-8)
- the pane-readability INSTRUCTIONS note (the 3.6 docs pass)

**Round-2 panel outcomes (2026-09-10, walked with the author — full
disposition in `review/DECISIONS.md`, reports in `review/3.6.0/`):**
- Snapshot-sequenced build: **Snapshot 0** — the popup as a static
  mock-up (synthetic data, no network, no model) for the author's taste
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
  focus — the author's live report), gated off while the file-manager
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
  deleted (one point, no scattered conditionals — the author's guard
  sprawl; the permissions consolidation lives in the 3.7.0 roadmap).
- E-stop recovery (the author's ruling, 2026-09-10, live-proven on
  their printer, overriding the investigation's readiness-gating
  alternative): after the stop the host refuses commands until the
  connection is cycled — the plugin disconnects and reconnects ONCE,
  automatically (1.5 s after the stop, `MonitorCommands.
  RECONNECT_DELAY_MS`), re-arming the monitor; the console's own
  connect/disconnect notes render the cycle. The rejected alternative
  (gating motion/setup on `klippy_state` readiness) stays on the
  table for 3.7.0's permissions consolidation if the reconnect ever
  proves insufficient.
- Thumbnails (Snapshot 2): one-shot fetches into a session temp dir
  with loading/ready/failed states and the no-spin-forever fallback
  (the author's ruling). Iteration 1 shipped THREE stacked defects,
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
- Recents reversal (the author, 2026-09-10): the greyed-out "No
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
  bar") — refined live by the author: ONE DROPDOWN PER FILTER
  CATEGORY, each self-contained with scrollable multi-select options;
  a category with an active filter turns SOLID BLUE as its active
  indicator. The active selections stay INSIDE each dropdown's menu —
  no chips band spilling into the form; the over-filtered empty state
  carries its own "Clear all filters" action.
- The grid is the flexible region: the popup must fit a small window
  without spilling over the emergency dock — the file list shrinks and
  scrolls ("if it dynamically shrunk the file list, we'd be good").
- The camera control bar reflows (wraps) when the window is crushed
  horizontally — "Reflowing that control is fine."
- Snapshot 0 iterates until the author is happy ("Let's keep iterating
  snapshot 0 until I'm happy") — no Snapshot 1 before the nod.
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
  (the author's live ruling).
- The sort slot shows the arrow only on the sorted column and nothing
  elsewhere — no "—" placeholder.
- Sort rule (the author's): exactly ONE column is sorted at a time;
  sorting another column cancels the current sort.
- The grid's two scrollers are axis-locked (vertical owns both
  columns; the horizontal one never intercepts a vertical wheel).

**Monitor width squeeze (the author's out-of-scope ruling,
2026-09-10):** when the stage is too narrow for the Webcam pane at
its minimum, the Information pane auto-collapses to make room. The
trigger is computed from FIXED constants (expanded Info width +
webcam minimum + status minimum) — never from the post-collapse
layout, so collapsing Information cannot move the goal post and no
hysteresis oscillation can form; re-expansion waits for the required
width plus a margin, so the boundary cannot jitter. While
auto-collapsed the toggle is inert with an explanatory tooltip.

**Snapshot 0 PINNED as the design (2026-09-10, the author): "I think
this is as perfect as we're going to get from iterating a mock. Pin
this as the design."** Snapshot 1 wires the real data behind this
exact face.

**Search/filter semantics (the author's functional rulings,
2026-09-10):**
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
  clear (the author's ruling).
- Direct page selection is CUT ("Mainsail doesn't have it"); "All"
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
- The scroll chevrons stay centred over the data — the author
  overruled the nudge into a gap ("otherwise they're not obvious
  enough").
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
moved to 3.6.1 by the author's ruling on 2026-09-11 ("too late to
continue on this and too risky") — see the 3.6.1 section for the
carried items: restart-button arming, the auto-improve-ETA opt-in, the
webcam liveness watchdog, ETA feed-forward, scroll-to-prompt and the
pause-list verified-pause-only semantics.

Snapshot 3 (2026-09-11): the mutations, uploads with progress and
verdicts, column config/resize/reorder with title floors, the
thumbnail queue with coalesced publishes and size-matched fetch, the
Esc/dialog state machine, and the panel re-review fixes (the print
watchdog's transition-is-success form, the directory delete route,
the clipped row viewport, the disconnected gates and the popup's own
note surface).

## 4.0.0 — Websocket transport (the author, 2026-09-11)

3.6.0 ends the v3 line. The author's ruling (2026-09-11): the
per-request HTTP polling is unacceptable in production — prints audibly
dwell while the plugin is connected (live-proven on their Voron: the
regression arrived with the 3.5.0 console poll, and every poll adds to
it). 4.0.0 moves the transport to Moonraker's websocket subscription
model: Klipper pushes object updates to Moonraker once per interval and
Moonraker fans them out to subscribers — one serialization shared by
all clients instead of one per client query. Everything in 4.0.1 waits
on this.

**The author's scope rulings (2026-09-11, verbatim):**

- "v4.0.0 - switch to websockets. This _should_ be a feature
  transparent change, so if any features do change, I need to know and
  approve first." — the transport swap is invisible to the user by
  design; any observable behaviour change stops at the author for
  explicit approval before it is built.
- "Also, I think there should probably be the option of enabling
  websockets vs sticking to HTTP, even though we know HTTP has
  performance issues." — a user-facing transport-mode choice:
  websocket subscription or the HTTP poll, both first-class.

**Phase-0 rulings (2026-09-11, walked with the author):**

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

**The author's note (2026-09-11, verbatim):** "Just a note to take into
account, I'm seeing a lot of the panel assess against Cura 5.9.1. The
current version of Cura is 5.13.x." — every compatibility claim in the
panel rounds is assessed against CURRENT Cura (5.13.x) and its bundled
PyQt6. The 5.9.1 pin in the tree is the capture theme only
(`tests/theme_assets/`, extracted from the 5.9.1 AppImage) — capture
fidelity, never the runtime baseline. Cura 5.13's bundled PyQt6 is
stricter than the dev container's newer PyQt6 about re-exports (the
QHostAddress lesson, pinned by
`test_qt_imports_name_the_module_that_owns_the_class`): the websocket
module-availability question must be answered against the real 5.13
bundle, not the container.

## 4.1.0 — Printer resilience and console polish

The old 3.6.1 items, re-homed by the author's final ruling
(2026-09-11): 3.6.0 ends the v3 line, the 4.0.0 websocket transport
comes first, and everything below ships from 4.1.0 onward.

- **Restart arming** — re-prime the start flow after a print ends or
  is cancelled, so a follow-up start cannot silently fail on stale
  state. Medium risk: it touches the print-state transitions.
- **Webcam watchdog** — detect a dead camera feed and restart the
  stream, with a veil while it recovers. Medium risk: camera
  restart/reconnect behaviour needs live-printer proof. The live
  shapes were captured from the author's Moonraker (2026-09-11):
  `server/webcams/list` returns per-camera relative `snapshot_url` /
  `stream_url` (`/webcam2/?action=snapshot`), `enabled` flags and
  `target_fps`.
- **ETA feed-forward** — prefer the printer's own remaining-time
  signal when it reports one, falling back to the slicer ETA.
- **Auto-improve-ETA opt-in** — an explicit setting that lets the
  model adjust the ETA from observed layer progress.
- **Scroll-to-prompt** — the console scrolls the prompt line into
  view when a command's response lands.
- **Pause-list verified-pause-only** — the scheduled-pause list shows
  only pauses that were actually verified.
- **Poll-cadence sliders (the author, 2026-09-11)** — the author's
  live report: prints slow down / pause at points while the plugin is
  connected; the per-second object queries are the prime suspect.
  The settings page gets sliders for the poll cadences (core
  interval — already a preference — plus the auxiliary and console
  cadences), backed by a relaxed printing policy: the auxiliary query
  steps down while printing (1 s → 2.5 s or the user's interval) and
  the per-second refresh stops re-asking save_config_pending on
  configfile.

## 4.2.0 — State & permissions consolidation

**State & permissions consolidation (the author, 2026-09-10):**
"Can I press this button when I'm printing, when I'm not homed, when
I'm paused, when I'm e-stopped?" — the guard logic has grown
scattered: the toolhead gate, the model's publish projections, the
QML's `enabled:` bindings, the power locks and the restart guards
each derive the same states ad hoc. Not a Klipper problem — the
plugin already polls the full state (`print_stats.state`,
`homed_axes`, `gcode_move`) and Klipper/Moonraker have NO permission
endpoint to query, so the table has to live plugin-side. The fix: ONE
pure policy module projecting the snapshot into named permissions
(can_jog, can_extrude, can_restart, can_power, can_start_print, …)
with the author's rulings as the table — every consumer reads the
same derivation, and the scattered conditionals collapse into a
single tested file. The e-stop assumption stays special: the ONE case
where the plugin must NOT trust the last poll — it lives at the
client's observation layer (emitted status reads as cancelled until
the printer says otherwise) and is documented there.

## 4.3.0 — Physical head in the Preview

What a web dashboard cannot do: show the real machine inside the slice.

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
  looking at the actual toolpath.
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
(1) a Preview status strip with pause/resume + temps + ETA; (2) the
already-computed off-path distance in plain words (`refined_fraction`
is thrown away today — "Head is 12.4 mm off the toolpath"; commanded-
not-sensed caveat: it cannot detect skipped steps); (3) pause-at-Z-
height + one-click pause-at-next-layer + the layer-to-mm readout
(`PhysicalLayer.height` has no consumer); (4) filament/colour-change
waypoints (M600, `; filament change`) on the layer timeline with
time-to-go; (5) a camera thumbnail in the Preview (investigate, don't
assume); (6) active-tool label + per-extruder path colouring.
Cross-cutting: the layer-hardening pack belongs IN 3.7.0 (the marker
inherits the resolver's numbers, and the `;LAYER:` flip without the
gate can increase wrong-layer risk); the version-drift gate's user
value is its presentation (permanent disabled-with-reason states); the
deuteranopia cheap 80% is fixing the default red/green pair. Out-of-
scope revisit: folder trees — confirm before 3.6.0 ships that files in
subfolders are at least listable and printable, or the file manager is
weaker than Mainsail's for anyone with a library.

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

## 3.6.0 candidate notes (settled with the author 2026-09-10)

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
  PAUSE retry — the author: "I'd hesitate to issue a retry in case it's
  just slow."
