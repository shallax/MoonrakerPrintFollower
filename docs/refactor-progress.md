# Refactor progress ledger

One row per batch. "Pushed" means the remote branch carries it, not that a
local tree contains it. A CI conclusion is quoted for the SHA it ran against;
a later push invalidates it.

Branch: `chore/v4.6.2`. Base when this ledger opened: `905c628`.

| batch | responsibility | result | commit | pushed |
|---|---|---|---|---|
| A | baseline repair | CI green | `a8ded46` | yes |
| B | file-browser QML | `FileManager.qml` 2949 -> 725 | `f6688b3` | yes |
| C | Monitor composition | `MoonrakerMonitor.qml` 3129 -> 892 | `0025b3c` | yes |

## Batch A — baseline repair

Two defects stood between the branch and a trustworthy baseline. Neither was
the failure the handoff predicted.

**The Linux blocker was a hard abort, not a test failure.** All three Python
matrix jobs exited 1 with no `FAIL` block because the interpreter aborted:
`test_qml_dashboard_interaction` mounted the information pane and Qt asserted
on the way into a slot. The double declared `setConsoleExpanded` as
`@harness.pyqtSlot()` with no arguments while the model declares
`@pyqtSlot(bool)` and the extracted console pane calls it with a bool from six
sites. `Q_ASSERT` is compiled out of release Qt, so Windows and macOS ran the
same broken call silently — the abort is Linux-only.

**The predicted index failure did not reproduce.** It passes on Linux in
isolation and in its batch, and it passed on Windows and macOS in the run that
fixed the abort. It is load-sensitive. The test already wraps
`IndexTasks.passive_yield`, which is the correct target; it was not edited.

## Batch B — file-browser QML

Five substeps. Leaves under `mpf/files/browser/`: toolbar, recents, directory
strip, search, filters, filter-option row, column chooser, grid, pagination.
`FileGridHeader.qml` and `FileRowDelegate.qml` were deliberately NOT split out:
the header and delegate consume the grid's own column geometry, so as separate
documents each would need ~15 handed-in bindings. The selection summary stayed
with the page strip for the same reason.

Verified by a pixel oracle: capture then `cmp` against the committed
screenshots, byte-exact after every step. 0 test methods removed or added.

## Batch C — Monitor composition

Five commits. The root keeps stage-level coordination — one active popover,
outside-click routing, cross-pane width decisions, the shared overlay frame —
and now only instantiates panes and popovers. Extracted: `TemperatureDetailPopover`,
`BedMeshDetail`, `ObjectPickerPopover`, `PrintFollowerPopover`,
`PauseScheduleView` (view only; scheduling stayed with the print model),
`InfoPane`, `InfoCollapsedReadout`, `StatusPane`, `StatusCollapsedReadout`.

The frozen narrow-window contract was not restructured: `cameraViewportWidth`,
the 226/366 costs, the locks, `applyNarrowWindowRules()` and the fit passes are
verbatim on the host.

Verified by the same pixel oracle, six scenes SAME.

## Outstanding

- `tests/test_gpu_canvas_isolation.py::test_live_layer_handoff_fades_previous_geometry_then_retires_it`
  asserts a QML animation advanced inside a fixed pump window; it failed once on
  macOS and passed on re-run. Load-sensitive assertion, not fixed. A green that
  needs a second attempt is not a fixed test.
- The pre-commit hook runs its six legs concurrently and its unit leg failed
  once under that load, then passed on an immediate re-run with no tree change.
  Same class as the above: load-sensitive, not diagnosed.
- Batches D-I not started.
