# Preview object-name banners handover

Status: implemented on `release/v5.1.0` and observed in local Cura 5.13 with
the current 20-object G-code. The physical-head marker, jog pad and other
Preview controls are planned for 5.2.0 in `ROADMAP.md`.

## Requested behaviour

- Upright object-name banners in Cura Preview, anchored to objects through
  Cura's supported camera projection and separated in screen space.
- A collapsible Preview bar with an enable checkbox and an
  all-banners/hovered-only choice.
- Hover raises the selected scene object's banner or every G-code object whose
  footprint contains the pointer, and fades all other banners. Scene models
  use Cura's depth-tested selection pass; G-code footprint picking is
  approximate because Cura does not expose per-object toolpath IDs.
- Per-object printed progress, countdown and projected finish time when source
  data supports them.

## Implementation

- `mpf/preview/PreviewPresentation.py` owns the dock, saved preferences, camera
  projection, scene-object selection-pass hover and fallback G-code anchors.
- `mpf/preview/PreviewObjectTagsHost.qml` renders the dock, leaders, dots,
  banners, progress strips, countdowns and finish times.
- `mpf/preview/ObjectNameProjection.py` places banners above anchors and moves
  crowded labels without moving their object endpoints.
- `mpf/gcode/ObjectWork.py` gathers bounded per-object filament checkpoints,
  last-work offsets, height and XY bounds from `EXCLUDE_OBJECT_START/END` or
  `;MESH:` markers during the existing one-pass scan. `IndexView` publishes
  centres, progress and a slicer-time-scaled ETA. The persistent index cache is
  version 15; earlier index caches rebuild.
- `PrintCoordinator._object_tag_values()` publishes object definitions and
  metrics only for the current print's exact plugin-loaded G-code path and
  matching index job key. Index XY bounds fill in missing Moonraker centres.
- `CuraIntegration.plugin_loaded_path` tracks that exact loaded path.

## Live finding and fix

The current 305 MB, 528-layer G-code took about 8.5 minutes for Cura 5.13 to
parse. The existing five-minute load watchdog released the busy state but kept
the file lease as `_load_watch_lease`; a later `fileCompleted` was correctly
absorbed. It did **not** set `plugin_loaded_path`, so the exact-file gate
withheld all object definitions and metrics. The index itself had 20 named
objects and valid XY bounds. The reported symptom was an enabled dock with no
banners.

`CuraIntegration._file_completed()` now sets `plugin_loaded_path` for both
on-time and watched late completions. Regression assertions were added to
`test_qt_cura_integration.py` and
`test_gcode_index_integration_coverage.py`. The late watcher already remains
active until completion; it is not a one-time abandonment of the load. The
five-minute watchdog still reports a timeout and clears the busy state so a
silently refused Cura load cannot lock out retry forever. The UI wording for
this slow-but-still-parsing state may need improvement.

## Verification

Focused real-QML and Preview tests cover placement, pane geometry,
current-print identity, footprint hover and late Cura load completion. Live
Cura 5.13 displayed all 20 banners after the current G-code finished parsing.
The expanded Object banners bar and collapsed Print Follower card were checked
in that window after the final padding change. The native UI harness does not
yet provide a matching object-marked print with selectable model meshes;
selection-pass boundary tests cover that path.
The final 5.1.0 tree passed all 4,829 local tests, the 99% overall and 95%
per-file coverage gates, all nine lint checks, package/source parity
verification, and the 34-scene capture determinism check.

## Hover and banner follow-up

Uranium's `selection` pass depth-picks selectable scene model meshes. Cura's
G-code-only `SimulationPass` does not expose per-object toolpath IDs through
that pass. G-code-only Preview now offers approximate footprint hover using a
camera ray at the displayed layer height. It raises every banner whose
footprint contains that ray, including overlapping objects; the leader dot
remains at each object's centre. A footprint can include empty space inside
its outline, so this mode does not claim to identify the visible toolpath
segment under the pointer. The dock tooltip names that limitation.

Klipper may uppercase the registered object names while G-code markers retain
mixed case. The Preview source merge now joins unique case variants, so those
two spellings do not create separate banners. Long names scroll back and forth
with cubic easing instead of being elided.

## Placement and hover visibility follow-up

The earlier placement search discarded an object after seven vertical lifts,
and also discarded any object whose projected centre was just outside the
window. In hovered-only mode it laid out every object first and then hid the
unhovered banners, so invisible banners still occupied the useful positions.

Placement now considers only the hovered objects in hovered-only mode and
tries a centred, straight upward leader first. Shared footprints can still
show multiple banners: the layout tries vertical separation, then bends the
leaders if it needs another column. All-banners mode keeps every finite
projected centre, including centres beyond the window edge, and falls back to
the least-overlapping in-window position when a screen is too crowded for
disjoint plates. Hovering now fades every other banner, regardless of distance.

The live 20-object plate showed that vertical-first placement almost never
bent a leader, even when the result had a long stack. Placement now compares
short bends with additional vertical lift; in a 20-object same-anchor stress
check, 14 leaders bend and all 20 banners remain placed. One unobstructed
banner remains centred above its anchor.

The controls now live in a compact bar near the bottom of Preview, beside the
Print Follower card. The bar reads the live card's left and bottom edges, so
it stays beside the card and aligns exactly with its bottom. The card's
`saveButton` component reports enough width to Cura's own
additional-components row for both card and bar, so Cura moves its G-code
control and other plugin additions to the bar's left. The header has Cura's
up/down chevron and expands upward to reveal both checkboxes. A
live attempt to insert a dropdown into Cura 5.13's Preview top row did not
mount, so the plugin leaves Cura's own top bar untouched.

New project emits Cura's workspaceLoaded signal and can leave stale
SimulationView layers briefly in memory. That signal clears the plugin-loaded
file identity; an unrelated file's fileCompleted signal does the same. General
scene-change signals must preserve the identity, because Cura can still add
nodes after fileCompleted and otherwise suppress every banner. Banners also
need the coordinator's exact active-print file match, so an unrelated G-code
or cleared project cannot borrow the active printer's object names.

## Preview card collapse follow-up

The Moonraker Print Follower card can now collapse from its title. The compact
state keeps the title, Attach/Detach when a toolpath exists, and Load current
print. Its detailed status, pause controls, load progress, and bed-mesh controls
return when expanded. The chevron uses Cura's own up/down icons. Both card
hosts share the saved `previewCardExpanded` setting, so switching between
Cura's action-panel and corner-overlay host keeps the same state. The card's
bottom edge remains fixed as its height changes; the object-banner bar follows
that edge.

The expanded Object banners bar now sizes itself from its checkboxes instead
of using a fixed height. It uses the Preview card's thick theme margin above
the heading and below the final checkbox, with a default theme gap between
the header and controls. The compact bar retains its original height.
