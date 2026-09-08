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
  `make generate_screenshots`, `make package`, `make install_hooks`,
  `make docker_exec ARGS="…"`, `make clean`. The targets are thin
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
- Deterministic captures: `tools/capture_monitor.py` plus the sibling
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
  settings tab (Connection / Following / Upload), each fitted to that
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
  the container and refreshes the committed copies in one step. The CI
  **Screenshot sync**
  job enforces this on every push: it rebuilds the pinned image,
  regenerates the captures inside it and fails byte-for-byte if the
  committed copies are stale (extra hand-captured files are allowed and
  never checked). CI still runs the capture
  scripts as a render smoke test (each script fails on a blank capture)
  but no longer ships the PNGs as artifacts. These capture the 2-D UI
  only: the 3-D Preview (bed-mesh overlay on a rendered model) needs a
  real Cura session — capture those by hand for marketing.

## Version bump checklist

When bumping the version (for example 3.1.0 → 3.2.0), every one of these must
change together:

1. `package.json` — `package_version`
2. `plugins/plugin.json` — `version`
3. `CHANGELOG.md` — a new section at the top, following the existing format
4. `README.md` — the release header (`**Release:**`) and the "What changed"
   section
5. Git tag — `v<version>`; the release workflow validates the tag against both
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

### Adding a collapsible section

The controls pane is a column of collapsible sections; `controlContent` in
`MoonrakerMonitorDashboard.qml` has `spacing: 0` on purpose — spacing lives on
the children, because a collapsed section's hidden content must contribute
nothing so the headers stack flush like Cura's accordion. Do not reintroduce
layout spacing; a `visible: false` layout child is excluded from layout, but
the remaining siblings' gaps come only from their own `Layout.*Margin`s.

Each section is two direct children of the pane's content column:

1. Header — a `CollapsibleSectionHeader` (the shared type in
   `plugins/CollapsibleSectionHeader.qml`, instantiated directly — no
   Loader) with `printerModel: root.printer`, `title`, `sectionId` and
   `sectionIcon`. The icon must be one Cura's own QML references (the
   header resolves `UM.Theme.getIcon(sectionIcon)` at runtime; guessing
   names risks an empty slot). Known-good names: Printer, House, Nozzle,
   Function, PrintQuality, Sliders, Spinner, Star, ThreeDots, CircleOutline,
   Settings, Save, Buildplate, MeshTypeNormal, Spool, Fan, Plugin,
   LinkExternal, Information, ChevronSingleDown/Left, ArrowDoubleCircleRight.
2. Content — a `ColumnLayout` directly after the header with
   `visible: root.printer == null || root.printer.sectionExpandedMap["<id>"] !== false`
   and `Layout.topMargin`/`Layout.bottomMargin` of `default_margin`. The
   missing-key check is deliberate: sections not in the map are expanded.
   Sections that should disappear entirely when their data is absent wrap
   header + content in a `Column` carrying the data's own `visible`
   condition.

Then update the pins in `tests/test_monitor.py` in the same commit: the
`CollapsibleSectionHeader` counts and the `sectionIcon:` counts per QML
file must match the number of sections. Section ids are unique across all
panes (the map is shared): print, setup, toolhead, macros, profiles,
tuning, fans, leds, pwm, power, system, save on the controls pane;
meshmap, job, temps, fansinfo, filament, objects, systeminfo, mcus on
the Information and Printer status panes. Persistence is automatic — the
stored map only records sections the user has touched.

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
  "controlsLocked": bool, "infoCollapsed": bool, "statusCollapsed": bool}`.
  The first shipped format was a flat section map; `_read_state` migrates
  it, so new fields must default with `bool(decoded.get(..., False))` and
  never break legacy reads.
- Writes are atomic (`.tmp` + `os.replace`) on every change.
- The model is the single owner: slots mutate fields, `_save_state()`, then
  `_publish()`. QML binds to the model properties and calls the setter slots
  — never a local default.
- Every new property and slot also goes into the surface lists in
  `tests/test_composed_components.py` (the properties string and the slot
  list) and the `_SIGNAL_KEYS` grouping in the model.

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

## Diagnostics

`PreviewMotion` can write a CSV trace of observations and display ticks for
smoothing analysis. It is off by default; set the
`MOONRAKER_FOLLOWER_SMOOTHING_TRACE` environment variable to a file name
(written inside Cura's cache directory, rolling at 512 KB) to enable it on
the next plugin start. Used to diagnose path-end stalls and smoothing
regressions on real printers; not needed in ordinary operation.

## Release gates

Local (also run by the pre-commit hook):

    python -m compileall -q plugins tools tests
    python tools/check_qml.py plugins
    ruff check plugins tools tests
    sh tools/check_qml_format.sh plugins/*.qml   # qt6-declarative-dev-tools; 6.4 has no --check
    python -m unittest discover -s tests -p "test_*.py"

CI runs the same checks (the `lint` job) plus the full suite including the
real-Qt tests (PyQt6 6.11.0). The release workflow on tag push additionally
builds reproducible archives and verifies source/package byte parity and the
Marketplace layout. Before tagging, run the smoke checks the harness cannot
cover: real QML rendering, native nozzle/bed-mesh integration, Cura
file-writer compatibility, multi-printer interaction and large files.
