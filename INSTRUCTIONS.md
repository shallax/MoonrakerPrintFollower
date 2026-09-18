# Development instructions

How changes are made in this repository. The architecture itself is described
in `ARCHITECTURE.md`; release history lives in `CHANGELOG.md`.

## Development environment

- Python 3.10+ with the standard library alone runs everything except the
  real-Qt tests, which skip automatically when PyQt6 is absent.
- For the Qt tests, create a venv with PyQt6 (CI pins 6.11.0). Some systems
  lack `libxkbcommon`; if `import PyQt6.QtGui` fails with that library
  missing, obtain it from the distro's binary packages and add its
  directory to `LD_LIBRARY_PATH`, and run the suite with
  `QT_QPA_PLATFORM=offscreen`.
- Linters: `ruff` (`pip install ruff`, configuration in `ruff.toml` —
  correctness core only, not style) and `qmlformat` from
  `qt6-declarative-dev-tools`. QML files must stay qmlformat-canonical:
  `tools/check_qml_format.sh` formats to stdout and diffs (qmlformat
  before Qt 6.5 has no `--check`), `tools/check_qml.py` verifies
  structure, and the token tests in `tests/test_monitor.py` must be
  written so the formatter cannot break them (pin semantics, not
  whitespace).
- The pinned, disposable dev container (`Dockerfile`) carries the whole
  toolchain — Ubuntu 24.04, git, Qt 6.4.2's qmlformat, Python 3.12,
  PyQt6 6.11.0, ruff 0.16.6 — and nothing else; the repository is
  bind-mounted at `/work`. The image is rebuilt from the Dockerfile
  (docker layer caching makes unchanged rebuilds instant), so deleting
  the container costs nothing. `tools/docker_dev.sh <command…>` runs
  any command inside it as the host user (the build-if-needed,
  `--user "$(id -u):$(id -g)"` and bind-mount incantation lives there —
  never run `docker run` by hand for this repo).
- Every procedure has a `make` target (see `make help`):
  `make build` (the full verification build — every gate plus fresh
  committed screenshots, i.e. what CI checks), `make lint` (structure,
  qmlformat, ruff, shellcheck, hadolint only), `make run_tests`
  (stdlib suite on the host, the real-Qt suite in the container),
  `make generate_screenshots`, `make package`, `make format`
  (qmlformat in the container), `make coverage` (plugins/ report,
  the gcov gate), `make snapshot_package` (build + verify + copy to
  /tmp/mpf.curapackage, ready to SCP), `make snapshot_quick`
  (the fast iteration path: lint + tests + package, no captures),
  `make install_hooks`, `make docker_exec ARGS="…"`, `make clean`.
  The targets are thin
  wrappers over the `tools/*.sh` scripts, which remain the single
  source of truth.
- The Makefile is the single entry point for procedures another
  developer would run: recurring work (docker invocations, unittest
  runs, capture refreshes, lint combinations) belongs behind a `make`
  target — a thin wrapper, with the real logic in `tools/*.sh`. Add a
  target only when it would genuinely benefit other users or
  maintainers of the project; one-off and session-specific commands
  should just be run as-is.
- Install the pre-commit hook with `tools/install_hooks.sh`; it runs the
  compile, structure, ruff, qmlformat and unit-test gates locally.
- Builds: `tools/build_curapackage.py` (Cura package) and
  `tools/build_marketplace_source.py` (Marketplace ZIP), verified by the
  matching verify tools; CI uploads both as artifacts.
- Seeded capture content: `tools/capture_monitor.py` feeds the dashboard
  with a bed mesh, endstop states, two console lines and ~10 minutes of
  synthetic temperature history (patching the model instance's own time
  module — never the global one), grabs a fourth scene with the chart
  pop-over open, and asserts the mini-chart region contains painted
  pixels (the requestPaint regression test).
- Deterministic captures: the harness freezes EVERY live input the
  scenes render — the formatter's wall clock is patched to a fixed
  instant (`FrozenDatetime`, patching every module object loaded from
  MonitorFormatting.py because the Qt runtime registers plugin modules
  under synthetic names), the model's time module is patched
  module-scoped during seeding, and the console pane renders no caret.
  `make verify_captures` runs the full capture suite TWICE in the
  pinned container and fails on any byte difference — a deterministic
  catch for leaks the committed-compare only catches by chance. Run it
  after changing anything the captures render.
- The capture scripts: `tools/capture_monitor.py` plus the sibling
  `capture_preview.py` / `capture_settings.py` / `capture_upload.py`
  render the real plugin QML offscreen using the REAL Cura and Uranium
  QML components and the real cura-light theme copied into
  `tests/theme_assets` (upstream LGPLv3 assets, refreshed deliberately —
  see its README). `tools/theme_support.py` materialises the capture
  tree: it splices the Python-registered types (stub ColorImage /
  I18nCatalog, an inert ToolTip stand-in), regenerates the UM qmldir
  across versions 1.0-1.8, and generates a literal `UM.Theme` singleton
  (Qt 6.11 pragma-Singletons must be self-contained). Lessons baked in:
  module resolution is last-import-path-wins (real tree last, stubs
  first), and PyQt6 collects inline `setContextProperty` temporaries —
  hold Python references. Outputs land in `dist/screenshots/*.png` (the
  CI artifact no longer includes them). `capture_settings.py` writes one PNG per
  settings tab (Connection / Following / Upload / Diagnostics), each fitted to that
  tab's Flickable content height — contentHeight is viewport-independent
  so measure-then-resize is stable, and the page's `UM.TabRow` appears
  as a composite class name (`TabRow_QMLTYPE_nn`), not `QQuickTabBar`.
  Two more hard-won lessons: at interpreter exit the QML engine
  re-evaluates bindings against already-collected context-property
  wrappers and spews nondeterministic "Cannot read property ... of
  null" TypeErrors AFTER the grabs (the PNGs are unaffected) — tear the
  scene down in dependency order (deleteLater the item, then drop the
  engine) while the wrappers are still referenced; and stubs must
  declare the same property surface as the real types they stand in for
  (a stub NetworkMJPGImage without imageWidth/imageHeight throws
  ReferenceErrors in every capture). The canonical copies for the README
  live in `screenshots/` and must be regenerated inside the dev
  container (whose pinned fonts make the output canonical — host font
  metrics differ): `tools/refresh_screenshots.sh` regenerates them in
  the container and refreshes the committed copies in one step — the
  `screenshots/` tree at the repo root is the canonical copy the README
  links; `dist/screenshots/` is only the harness's working directory. The CI
  **Screenshot sync**
  job enforces this on every push: it rebuilds the pinned image,
  regenerates the captures inside it and fails byte-for-byte if the
  committed copies are stale (extra hand-captured files are allowed and
  never checked). CI still runs the capture
  scripts as a render smoke test (each script fails on a blank capture)
  but no longer ships the PNGs as artifacts. These capture the 2-D UI
  only: the 3-D Preview (bed-mesh overlay on a rendered model) needs a
  real Cura session — capture those by hand for marketing. Automating
  that (running real Cura under a virtual display) was considered and
  deliberately rejected: the cost and fragility are not worth it.

## Repo hygiene — the standing rule on addresses

No real machine addresses go into the git repo (the rule,
2026-09-11). Fictional placeholders (``voron-0.2.local`` in the capture
fixtures) and unreachable LAN-internal names are tolerable; public
hostnames, proxy endpoints and any host an outsider could reach are
not. API keys never enter any tracked file (the gitleaks gate and the
literal-key pin enforce it). Real deployment details belong in the
git-ignored ``review/`` log, not in the roadmap, docs or code. If an
address slips in despite the rule, it is written OUT OF HISTORY, not
merely fixed forward (the 2026-09-11 amendment).

## Release workflow

New releases follow the `/new-feature` skill (`.claude/skills/new-feature/SKILL.md`):
plan with real push-back → one round-1 critic
before going deep → build with tests → a six-persona panel
(architecture/UX/engineering/product/security and the
Klipper/Moonraker/Cura domain expert, read-only, findings funnel back
through the maintainer; a 3D-printer enthusiast/pro-user persona joins
from 3.6.0 on, feeding next-release feature planning rather than
gate-calls) → decisions logged in `review/DECISIONS.md` (git-ignored) →
round-3 verification → the snapshot loop (live testing of
`/tmp/mpf.curapackage`; commits and pushes hold until it is
confirmed good) → ship via PR.

## Version bump checklist

When bumping the version (for example 3.1.0 → 3.2.0), every one of these must
change together:

1. `package.json` — `package_version`
2. `plugins/plugin.json` — `version`
3. `CHANGELOG.md` — a new section at the top, following the existing format
4. `README.md` — the release header (`**Release:**`) and the "What changed"
   section
5. `plugins/WhatsNew.py` — a new head entry (headline + user-facing items)
   and the frozen-history pin in `tests/test_whatsnew.py` recomputed: a
   shipped release's notes are FROZEN — later releases add their own entry,
   never edit the older ones
6. Git tag — `v<version>`; the release workflow validates the tag against both
   version fields and fails on mismatch

The version test asserts `package_version` and `plugin` `version` stay in
sync; the release workflow asserts both equal the git tag, so no test edit is
needed per release.

`sdk_version` in `package.json` is a compatibility floor, not a release number:
change it only when the Cura SDK floor moves (see `tests/test_sdk_compatibility.py`).

## Test suite organisation

- One file per domain (`test_architecture.py`, `test_session.py`,
  `test_print_state.py`, `test_monitor.py`, …).
- Never name test files or classes after versions, releases, or individual
  fixes (no `test_v31_*`, no `*_regressions`).
- New tests join the existing domain file; a new domain gets a new file.
- Tests that need real Qt are guarded with
  `@unittest.skipUnless(QT_AVAILABLE, ...)`. They skip in stdlib-only local
  runs and run in CI, where `ci.yml` installs PyQt6.

## Architecture contract

- `ARCHITECTURE.md` is the implementation contract, not a roadmap. Code
  changes that alter a boundary must update the doc and its tests together —
  never rename source strings just to make tests pass.
- Component import rules live in the allowlist in
  `tests/test_architecture.py`; changing a component's dependencies updates
  that allowlist.
- New components are constructed in `FollowerRuntime.py` and wired by
  `PrintCoordinator.py`; add them to the ownership map in `ARCHITECTURE.md`.

## Monitor controls pane

### Standing UI rules

Two rules govern every control on the Monitor tab; both are pinned by
`tests/test_monitor.py` (`test_no_controls_disappear_controls_disable`
and `test_disconnected_disables_every_monitor_control`) — update the
pins in the same commit as any change to a control.

- **No reflow, ever**: no controls disappear, ever — only
  disablement/enablement. Nothing may cause the UI to reflow unless
  the user explicitly asks for it (expanding/collapsing sections,
  resizing things, etc). The
  reasoning: a control vanishing mid-interaction moves the button
  under the pointer — genuinely dangerous during jog nudges. So
  state-gated controls render permanently and toggle `enabled`; status
  lines are permanent single-line slots (fixed height, elided,
  no-wrap) whose text changes; and space that must be reserved is held
  with `opacity`, never `visible`. Capability-static gates (a feature
  the machine simply lacks, changing only on a printer switch) keep
  `visible:` — they are whitelisted in the structural test.
- **Disconnected disables everything** (the ruling): while
  the printer is disconnected every Monitor control disables — the
  emergency stop included — via section-level
  `enabled: root.printer == null || (!root.printer.controlsLocked &&
  root.printer.monitorConnected)` gates. The console is the exception
  on the input side only: the transcript stays scrollable, selectable
  and copyable (its section keeps `enabled: root.printer != null`),
  the well greys out, and only the input row and Send/Clear disable.
  The camera veils while disconnected and carries a Live badge while
  live, and the console feed notes every connect/disconnect with its
  own `#` lines. The reasoning: nothing should look actionable when
  the printer cannot act on it, while the previous session's feed
  stays readable evidence.

### Adding a collapsible section

Since 4.3.0 every collapsible section is its own property-driven
component; the panes are thin shells. A new section is a new QML
file, never inline content:

1. The component (`FooSection.qml`) — a `ColumnLayout` root
   (`id: root`, `spacing: 0`) with `property var printerModel: null`,
   then the shared `CollapsibleSectionHeader`
   (`plugins/CollapsibleSectionHeader.qml`, instantiated directly —
   no Loader) with `Layout.fillWidth: true`,
   `printerModel: root.printerModel`, `title`, `sectionId` and
   `sectionIcon`. The icon must be one Cura's own QML references (the
   header resolves `UM.Theme.getIcon(sectionIcon)` at runtime; guessing
   names risks an empty slot). Known-good names: Printer, House, Nozzle,
   Function, PrintQuality, Sliders, Spinner, Star, ThreeDots, CircleOutline,
   Settings, Save, Buildplate, MeshTypeNormal, Spool, Fan, Plugin,
   LinkExternal, Information, ChevronSingleDown/Left, ArrowDoubleCircleRight.
2. Content — a `ColumnLayout` with `Layout.topMargin`/
   `Layout.bottomMargin` of `default_margin`, `Layout.leftMargin` of
   `narrow_margin + section_icon / 2`, `Layout.fillWidth: true`, and
   `visible: root.printerModel == null || root.printerModel.sectionExpandedMap["<id>"] !== false`.
   The missing-key check is deliberate: sections not in the map are
   expanded. The margins ride the gated content, so a collapsed
   section contributes nothing and headers stack flush. NO spacer
   Items, NO anchors, NO `width: parent.width` inside the layout
   root — the layout manages its children (probe-verified: a plain
   Column root counted the invisible children's implicit heights
   into the pane's scroll length, and a width binding inside the
   layout broke to 0).
3. The host instantiation is a SIBLING in the pane's content column:
   `FooSection { Layout.fillWidth: true; printerModel: root.printer }`.
   Never nested inside another section's instantiation (valid QML,
   wrong layout — the adjacency pin catches it). The id boundary runs
   BOTH directions: content never reads the host's ids, AND the host
   never names the section's ids — it reaches the section through
   the instantiation id (an accessor function) or a signal. The
   capability gates (hide while the data is absent, refuse while the
   permission is absent) ride the SECTION body.

Then update the pins in `tests/test_monitor.py` in the same commit:
the `CollapsibleSectionHeader` counts and the `sectionIcon:` counts
per QML file plus the totals, and the section-id haystack — a moved
section decrements one file and increments another, and the totals
catch a dropped section that a per-file pin alone would read as
"moved". Section ids are unique across all panes (the map is
shared). Persistence is automatic — the stored map only records
sections the user has touched.

### Collapsing a whole pane

Every pane follows the same recipe: a header row whose frozen pane title
sits beside a `Cura.SecondaryButton` toggle (the toggle hugs the edge the
pane collapses into — right for rightmost panes, left for leftmost), a
`Flickable` whose `visible` binds to the pane's collapsed property, and
a collapsed-strip wrapper: an `Item` anchored to the **header row**
(`anchors.top: <header>.bottom + thin_margin`,
`anchors.horizontalCenter: parent.horizontalCenter`) sized from the
label's extents (`width: label.implicitHeight`,
`height: label.implicitWidth`), with the label `rotation: 90` and
`anchors.centerIn` — the rotated text then occupies the wrapper exactly,
starting under the header. Never anchor to the toggle inside the header:
QML only allows anchoring to a parent or sibling, so that anchor is
silently dropped and the title floats. The pins in `tests/test_monitor.py`
enforce the header-row anchors. The pane widths collapse to
`<toggle>.width + 2 * thin_margin`.

The Printer status strip adds the connection dot: it leads the rotated
title in its own 24 px band at the top (the title shifts down 12 px
via `anchors.verticalCenterOffset`), matching the expanded header's
dot-before-title order with a space-width gap. The dot binds
`connectionDotColour` from the pane root; the collapsed-strip pin in
`tests/test_monitor.py` enforces it.
The state is a model bool: add it to
`_read_state`/`_write_state`/`_save_state` in `MoonrakerMonitorModel.py`
(with a `bool(decoded.get(..., False))` default), a
`value_property` + signal group + `set…Collapsed` slot, the QML root
binding (`property bool …Collapsed: root.printer != null ?
root.printer.…Collapsed : false`), and the surface lists in
`tests/test_composed_components.py`.

Plugin-drawn glyphs (see `plugins/PadlockLocked.svg`,
`PadlockUnlocked.svg` and `Power.svg`) must carry no hardcoded fills:
`UM.ColorImage` injects the theme colour into the root `<svg>` element,
so the glyph follows Cura's theme and scaling automatically. Reference
them from a header with `sectionIconUrl: Qt.resolvedUrl("Name.svg")` or
from a button with `iconSource: Qt.resolvedUrl("Name.svg")`, resolved
relative to the QML file's directory.

### Persisting monitor panel state

State lives in one plugin-owned JSON file next to cura.cfg —
`Resources.getStoragePath(Resources.Preferences, SECTIONS_FILE_NAME)` in
`MoonrakerMonitorModel.py` — never Uranium's preference store, which drops
reads and writes on unregistered keys, only persists on Cura's own save
cycle, and mangles values through configparser.

- File shape: `{"sections": {...}, "controlsCollapsed": bool,
  "controlsLocked": bool, "infoCollapsed": bool, "statusCollapsed": bool,
  "consoleHeight": int, "fileManagerColumns": {...},
  "toolhead": {...}, "whatsNewSeen": ...}` — the model's save payload
  rewrites these WHOLE top-level keys per save. `temperatureChart` is
  read-only here (it migrated into the per-printer record; the
  migration deletes the key once). The UI-state store's `sections`
  key lives as a top-level SIBLING of these: anything nested inside
  it gets erased by the next whole-key save. New fields default via
  `bool(decoded.get(..., False))` and the column config goes through
  `FileManagerPolicy.normalise_columns` (the file manager owns it; the
  model only merges and saves).
  The first shipped format was a flat section map; `_read_state` migrates
  it — recognised ONLY when every value is a bool, so a document that
  lacks `sections` and carries the UI-state store's sibling keys never
  hydrates them as sections. New fields must default with
  `bool(decoded.get(..., False))` and never break legacy reads.
- The FILE is the `StateStore`'s (4.2.0): writes are atomic
  (`.tmp` + `os.replace`, O_NOFOLLOW + 0o600) read-modify-write
  MERGES on every change — foreign keys survive (4.3.0's UI-state
  store consumes the same file). `write(..., delete=(...))` drops
  named keys inside the merge — the chart migration's only job. The
  full-document replace is GONE: it was the sibling rule's single
  exception and silently erased every other consumer's keys. A
  missing file is the first run — silent; genuine failures note once
  per session through the console.
- The model owns its values: slots mutate fields, `_save_state()`,
  then `_publish()`. QML binds to the model properties and calls the
  setter slots — never a local default. The sections map is the
  exception (4.3.0): it persists through the `UiStateStore`, the
  file's second consumer — the model hydrates it but no longer
  writes it.
- Every new property and slot also goes into the surface lists in
  `tests/test_composed_components.py` (the properties string and the slot
  list) and the `_SIGNAL_KEYS` grouping in the model.

### Rehydration order

Restore persisted values into the model eagerly — before their widgets
even exist. A sensor colour saved yesterday must apply to the samples
that arrive today, the console history is just a list, and the
probe-points toggle is a plain bool: all are safe to rehydrate at model
construction. The trap is the reverse direction: pruning, validating or
projecting persisted state against a live set that is still EMPTY
(startup before the first auxiliary reply) silently discards or
misreads what the user saved. The rules: never prune persisted entries
while the live set is empty; never treat "no data yet" as "nothing
configured"; and pin the set-before-arrival scenario with a Qt test
(see `test_chart_config_set_before_history_arrives_still_persists`).
The same rule covers widget INSTANCES created after the model already
holds the value: a freshly instantiated `BedMeshMap` must rehydrate its
`showProbePoints` from the printer property at creation — waiting for a
toggle event left the checkbox on while the freshly opened map drew no
dots (the 3.5.0 probe-points persistence bug).

### QML gotchas

Each of these cost real iterations; the pins and recipes above exist so
they cannot recur silently.

- **Anchors only reach parents and siblings.** `anchors.top:
  <toggle>.bottom` where the toggle lives inside a sibling header row is
  illegal — the engine logs "Cannot anchor to an item that isn't a
  parent or sibling" and *silently drops the anchor*. Anchor to the
  header row instead; `tests/test_monitor.py` pins the header-row
  anchors. Related: expression reads of anchor lines
  (`y: item.bottom + margin`) evaluate to 0/NaN — only the anchor form
  positions reliably; a probe with the real engine proved both.
- **On direct instantiations, assign — never declare.** The old Loader
  pattern (`property string title: …`) worked because the Loader injects
  its declared properties into the loaded item. On a direct
  `CollapsibleSectionHeader { … }` instantiation the same syntax
  *declares a new local property*, silently leaving the type's own
  property at its default: empty titles, every header reading
  `sectionExpandedMap[""]`. The negative pins (`assertNotIn("property
  string sectionId:", …)`) guard it.
- **Layout spacing belongs on children, not the layout.** A collapsed
  section's `visible: false` content is excluded from the layout, but
  layout-level `spacing` still adds dead space between the remaining
  siblings — the "gap between collapsed sections" bug. Pane content
  columns keep `spacing: 0`; every child carries its own
  `Layout.topMargin`/`bottomMargin`.
- **`Layout.*` is ignored outside Layouts.** A plain `Column` neither
  honours `Layout.alignment` (the camera bar's centring bug) nor
  `Layout.fillWidth`/margins (the status pane's zero-width headers). If
  an item must sit in a plain positioner, position it with anchors.
- `sectionExpandedMap` is a QVariant-wrapped dict; `map[id] !== false`
  is the expanded check, not `=== true`. A missing key means expanded.
- Rotated text spins around the label's centre: to place vertical text
  precisely, wrap it in an `Item` whose width/height swap the label's
  implicitHeight/implicitWidth and centre the label inside it.
- Keep icon slots fixed-size (`visible:` toggles, `width:`/`height:` stay)
  so titles stay aligned even if a theme lacks an icon.
- **Hover stickiness in Flickables.** `containsMouse` can stay true after
  the pane scrolls under a stationary cursor, latching the header tint.
  Drive hover from `onEntered`/`onExited`, clear it in `onPressed` and
  re-check in `onReleased` (see `CollapsibleSectionHeader.qml`).
- **Icon sourcing.** The set of icons Cura's *QML* references
  (`getIcon("…")` in the sources) is a subset: setting categories carry
  icons through the printer definitions (`resources/definitions/
  *.def.json` — e.g. Cooling uses `Fan`), which the QML grep misses.
  Check both before drawing a plugin glyph.
- `tools/check_qml.py` must pass before commit — it catches unbalanced
  braces and stray bare-type declarations that text scanners miss but the
  real QML engine rejects.
- **Component-scoped ids are invisible outside their Component.** The
  mesh detail map's `id: meshDetail` lives inside `Component { id:
  meshContent }`; three outer-scope call sites referenced it and threw
  `ReferenceError` — which also killed the auto-close statement that
  followed the throw. Inner components see outer ids, never the
  reverse; put refresh `Connections` inside the component.
  `tests/test_monitor.py` pins `meshDetail.refresh()` to exactly one
  in-scope call.
- **Pop-over anchoring is a single consistent offset.** Both pop-overs
  open at `x: cameraArea.x + margin, y: margin` — clear of the
  Information-pane openers, so a second click dismisses without moving
  the mouse (the chosen position).
- **Cursor-following tooltips live OUTSIDE the clipped card.** The
  chart's hover values are a floating, root-scoped `Item` (z above the
  pop-overs) positioned from the chart's cursor point mapped with
  `mapToItem(root, …)`, proxied through properties on the pop-over
  shell. A tooltip is allowed to overflow any boundary; putting the
  readout inside the card made the pop-up stretch past the window
  instead.
- **Never combine `fixedWidthMode: true` with `Layout.fillWidth`** on the
  same button: the two fight every layout pass and sent
  `QQuickGridLayoutBase` into an endless, memory-eating invalidate loop
  that froze pane collapses (the 3.5.0 capture runaway). Likewise never
  bind a pop-over shell's height to its content column's implicit height
  while the content uses `Layout.fillHeight` — the same cycle.
  `tests/test_monitor.py` pins both.

### QML change discipline (learned the hard way)

- Prefer structural edits with exact anchors read from the file over
  text-scan surgery; brace-scanned edits have broken the monitor QML
  twice (naive check_qml and the unit suites both passed).
- Before ANY scripted text surgery (regex or string replaces over a
  large span), copy the file to `/tmp/mpf` first — or commit — so a
  mangle is always revertible. The 2026-09-11 model mangle ate the
  whole `_publish` function and only the previous package's copy made
  the restore exact.
- Before committing QML changes, load the document on the REAL engine
  (the capture harness's theme/materialise setup) — structural errors
  and unresolved names only surface there. A binding's unqualified
  name does NOT resolve through the visual parent (ReferenceError);
  qualify through ids.
- Check the capture log explicitly (`tools/refresh_screenshots.sh`
  exits 0 AND reports all scenes) before committing; a silent capture
  failure makes CI's screenshot sync fail.
- For animation/layout behaviour, add an engine-level test that
  instantiates the pattern and measures it (see
  `test_sweep_phase_advances_on_the_real_engine`), rather than
  debugging through the full-suite loop.
- Bindings on the ROOT object do not track setProperty-driven changes
  to the root's own properties (engine-proven: the root's `visible`
  stayed frozen while a child's identical binding flipped). Gate with
  an inner item/row instead; children may reference the root's id.
- `tools/check_qml_engine.py` loads every plugin QML document on the
  real engine and fails on component errors and engine diagnostics
  (ReferenceError, dropped bindings, binding loops, TypeError,
  non-existent-property assignments); it runs in the LOCAL lint gates
  and the pre-commit hook only — it is not part of any GitHub
  workflow, and it is an INSTANTIATION smoke test: documents load
  with a null printer and closed pop-overs, so guarded branches and
  Loader-gated content never evaluate (a live-model variant is 4.0.0
  debt). External context contracts
  (manager, actionDialog, OutputDevice) are stubbed there —
  the manager stub covers BOTH the machine-action settings surface and
  the upload-dialog surface (the dialog reads the same `manager`
  context property), the stubs are pyqtProperty-declared (dynamic
  setProperty values are not visible to QML bindings as typed
  properties), and every stub QObject keeps a Python reference for the
  whole run (PyQt6 releases unrooted wrappers and QML then reads null).
  Documents must tolerate standalone instantiation without a parent
  (`parent != null` guards) — the gate creates them that way.
- The monitor's progress bars and sliders are plugin-owned outline
  components (`OutlineProgressBar`, `OutlineSlider`), not the themed
  `ProgressBar`/`Slider`: the themed controls render a black slab in
  the inactive-window palette. Tracks are transparent with a lining
  border and the fill is Cura's brand blue (`primary`, the same accent
  as buttons and slider handles). New bar/slider uses go through the
  outline components; the unit pins forbid bare themed ones. The
  capture harness seeds `virtual_sdcard.progress` so the bars show a
  fill, and asserts accent-blue pixels inside every visible bar and
  slider — a fill that stops rendering fails the screenshot job.
- **The no-reflow rule (the 2026-09-10 ruling):** a control
  never disappears — every state lives in `enabled`, never `visible`
  ("no controls disappear, ever. It's only disablement/enablement").
  Nothing reflows unless the user asked for it (section collapse,
  resize): state-dependent status lines occupy permanent single-line
  slots whose TEXT changes, and reserved space uses opacity, never
  visibility. **Reasoning:** the jog-reflow hazard (the live report,
  2026-09-09) — while hammering a toolhead move, the
  pause/cancel buttons (and other state-gated controls and labels)
  vanished and reappeared as printer state changed, so the nudge
  button UNDER THE POINTER could move mid-click. Incredibly dangerous
  during nudges. Every QML change must therefore keep the geometry
  constant outside user-initiated actions. `test_no_controls_disappear_controls_disable`
  pins the banned `visible:` patterns and the replacement `enabled:`
  bindings; the explicit carve-outs (data-driven section gates —
  fans/LEDs/macros/power sections on machines without them, the
  scheduled-pause list, the temp-chart first-data swap) are listed in
  the test and in `review/DECISIONS.md` round 6 for review.
- **The tooltip rule (the 2026-09-18 ruling):** EVERY tooltip follows
  Cura's placement pattern — a `UM.ToolTip` child of the annotated
  control, positioned BELOW it with the arrow at the control's
  top-centre (`targetPoint: Qt.point(parent.width / 2, 0)`, `x: 0`,
  `y: parent.height + <margin>`), shown on the control's own hover
  (`visible: parent.hovered` for Button-family controls; a
  `HoverHandler` for surfaces without `hovered`, one unique id per
  tooltip per scope). Never the native `tooltip:` property, never a
  `UM.TooltipArea` over a control — a popup that can cover its
  control swallows the click when the pointer crosses it (the live
  find: a reset button's tooltip ate presses, and the native-property
  popups pointed at the wrong control entirely). `test_monitor`'s
  tooltip-discipline pin fails the leg if either form returns.
  Passive readouts get the same pattern, not an exemption.
- **The Uranium-controls-first rule (the 2026-09-18 ruling):** where
  possible, use Cura's native controls — `UM.CheckBox`, `UM.ToolTip`,
  `Cura.ComboBox`, `Cura.RadioButton` and the rest — over raw Qt
  Quick Controls or bespoke drawn ones. The native controls carry the
  theme (light and dark) for free; a bespoke control forks that
  theming and drifts (the live report: the popover rows' drawn
  checkbox, the tri-state selector, the file-manager select-all and
  row checkboxes, the filter markers and the page-size radios all
  drifted from the native set). Bespoke drawing stays only where no
  native control fits (e.g. the drag handles; the multi-select filter
  rows, which `Cura.ComboBox` cannot express).

### Verifying QML geometry

When a layout claim matters (rotated text, anchors), don't argue with the
code — measure it. A throwaway probe with the real engine settles
positioning in seconds: instantiate the same structure in a
`QQmlComponent` under `QGuiApplication` (offscreen platform), dump the
items' `x/y/width/height` from `Component.onCompleted`, and compute the
rotated visual bounds (`rotation: 90` maps width→down, height→left
around `transformOrigin`). The probes used for the collapsed-title and
anchor diagnoses live in this session's scratch, and the pattern is
reproducible with only PyQt6.

## Comment discipline

Comments state WHY, briefly:

- One or two lines for the ordinary case; a documented trap may earn
  four or five; nothing earns ten. If a comment is heading that way,
  the explanation belongs in `ARCHITECTURE.md` (the design) or here
  (the lesson), not inline.
- No play-by-play of what the code plainly does, no restating the
  design doc, no quoting people (the ruling: comments speak
  in their own voice).
- The design notes live in `ARCHITECTURE.md` and `ROADMAP.md`; inline
  comments carry only the local why. New code matches its file's
  comment density — the ~20% neighbourhood the plugin settled at is
  the ceiling, not the target.

## Diagnostics

`PreviewMotion` can write a CSV trace of observations and display ticks for
smoothing analysis. It is off by default; set the
`MOONRAKER_FOLLOWER_SMOOTHING_TRACE` environment variable to a file name
(written inside Cura's cache directory, rolling at 512 KB) to enable it on
the next plugin start. Used to diagnose path-end stalls and smoothing
regressions on real printers; not needed in ordinary operation.

The settings' Diagnostics tab drives `LeakProbe`: the main toggle arms a
10-second sampler to `~/moonraker_leak.log` (physical footprint, resident
size, stage, layer and camera gauges each tick, plus QML class diffs and
plugin collection sizes), the Python allocation trace is a separate
heavier opt-in sampled once a minute, and the camera kill-switch stops
the stream for isolation tests. Used for leak hunts and field reports;
not needed in ordinary operation.

## Release gates

Local (also run by the pre-commit hook):

    make lint        # compileall, check_qml(.py + engine), qmlformat, ruff,
                     # shellcheck, hadolint, gitleaks — one container pass
    make run_tests   # every suite once, verdict + failures extracted from that single pass

CI runs the same checks (the `lint` job) plus the full suite including the
real-Qt tests (PyQt6 6.11.0). The release workflow on tag push additionally
builds reproducible archives and verifies source/package byte parity and the
Marketplace layout. Before tagging, run the smoke checks the harness cannot
cover (the full matrix from the panel round, restored after a
transcription drift):

- Real QML rendering on the tag-built artifact (not a stale local build).
- Native nozzle/bed-mesh integration in the Preview.
- Cura file-writer compatibility (save/upload paths).
- Multi-printer interaction and large files.
- Chart continuity across preheat / print / pause / target-change.
- Power-area plausibility (heater power bands on the chart).
- Persistence across a Cura restart AND a printer switch (the
  clean-install restart half is automated now — `MODE=firstinstall`,
  TESTING.md §3).
- Multi-hotend / chamber mini-widget selection.
- A dense 25x25 bed mesh crosshair.
- The bed-mesh pop-over open with its probe-points toggle (the one
  pop-over path the capture harness does not exercise).
- Escape-dismiss hand-test on the pop-overs.
- Multi-hour Canvas/CPU sanity while printing.
- One old + one current Cura (the README claims Cura 5.0-5.13 /
  SDK 8.0-8.12).
Once the workflow's tag-built artifacts exist, unpack the curapackage and
grep the shipped QML/Python for the verification markers — no `BISECT`,
no `visible: false` console gate, `GET` (not POST) on the endstop query,
no `~` backup files — before announcing; a stale local `dist/` build can
look identical to the real artifact by eye.

## Development install loop

`make dev_install` (tools/install_dev.sh) symlinks this checkout's
`plugins/` into Cura's user plugin directory
(`~/.local/share/cura/<version>/plugins/MoonrakerPrintFollower`), so
edits appear on the next Cura restart — no package download, unzip or
drag. The symlink shadows the packaged copy; `rm` it to go back to the
installed package. QML/plugin changes still need a Cura restart (Python
modules and the QML engine cache), which is the floor the loop can
reach without in-process reload machinery.

A plugin must never self-update at runtime: replacing its own files
while loaded is fragile (file locks, half-written states, no rollback)
and Marketplace rules expect updates to flow through the Marketplace
channel.

Diagnostic traces are per-printer settings in the plugin's
configuration: "Log layer resolution" writes the layer inputs every
5 s to Cura's log; "Log HTTP requests" logs every request. Both off
by default — request FAILURES always log a warning regardless.
