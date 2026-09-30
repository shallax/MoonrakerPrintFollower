# Handover: finishing the coherent-decomposition refactor

## What this is

`shallax/MoonrakerPrintFollower` (Cura 5.13 plugin, PyQt6/QML, for Klipper/Moonraker printers) is
mid-way through a large decomposition on branch **`chore/v4.6.2`**. The goal: pull genuinely
independent responsibilities out of the remaining monoliths into owners with explicit inputs, while
preserving behaviour, performance and the public Qt/QML API. **No new architecture. No behaviour
change.**

The original brief (batches A–I) exists as a long document; the sections for the remaining batches
are reproduced verbatim below so you do not need it.

## State at handover

Verify this yourself before starting — do not trust it:

```sh
git fetch origin
git rev-parse origin/chore/v4.6.2     # expect fa50016 + Batch G's commits
git status --short                     # expect clean
```

| batch | result | commit | CI |
|---|---|---|---|
| A baseline repair | Linux abort fixed | `a8ded46` | green |
| B file-browser QML | `FileManager.qml` 2949 → 725 | `f6688b3` | green |
| C Monitor composition | `MoonrakerMonitor.qml` 3129 → 892 | `0025b3c` | green |
| D camera / toolhead / Preview / dashboard | 1403→221 · 993→401 · 1211→354 · 1181→980 | `a8ece19` | green |
| E renderer extraction | `MoonrakerMonitorModel.py` 4486 → 3015 | `9e89200` | green |
| F publication transaction | `_publish()` 438 → 5 lines; model 3015 → 2798 | `fa50016` | green |
| G python orchestration | `FileManager.py` 1317→1026, `GCodeIndexService.py` 1742→1399, refresh() split into phases; +`ThumbnailCache.py` 264, `PreparedSession.py` 350, `ReplyBodyReader.py` 56 | `96a2026` | run 36721180076, pending at handover |
| H PlateProgressFace.qml | **not started** | — | — |
| I test suite and harness | **not started** | — | — |

`docs/refactor-progress.md` in the repo is the authoritative ledger — one row per batch, with the
couplings each batch found that its brief did not name. **Read it first.** It is updated as part of
normal batches.

## Remaining work, verbatim

### Batch H -- PlateProgressFace.qml, last and conservatively

Do this after the Python renderer owner is stable. Do not change both sides of the renderer
boundary in the same large patch.

Read `PlateExactComposition.js` before inventing another presentation-policy module. Preserve its
existing authority; do not create two versions of "which frame may be displayed."

Extract in this order:

1. Pure view/coordinate calculations with explicit scalar/record inputs.
2. Pure Canvas drawing helpers that receive the context and required geometry/style explicitly.
3. Presentation decision helpers that truly can be expressed without reading arbitrary QML IDs.
4. Independent visual leaves, such as toolhead marker and zoom scope.
5. Larger raster-stack or gesture components only after their inputs, outputs and lifetimes are
   narrow and testable.

Plausible modules are `PlateViewPolicy.js`, `PlatePainter.js`, and carefully chosen visual leaf
files. These names are not permission to move impure closures into a JS library that still secretly
reaches back into the old root.

Keep one owner for interdependent interaction, world/anchor epoch, retained-prefix receipts,
delivered composition and texture-readiness state until there is evidence a further split improves
clarity. Do not turn one state machine into a web of property-change handlers across components.

Preserve:

- Full/current/prefix/retained/ghost/travel composition arbitration.
- Back-scrub to zero, forward completion, layer transitions and cold prepared-layer arrival.
- Mid-gesture zoom/pan, captured navigation assets and return to exact presentation.
- Mini/popover isolation, GPU and software paths, thick lines, color modes, travel/retraction settings.
- DPR behavior, asynchronous image readiness and retained assets across delayed delivery.
- Motion smoothing, toolhead centering and no per-frame Python callbacks introduced by extraction.
- Prevention of flicker, gaps, stale pixels, duplicate strokes and earlier Windows regressions.

**Acceptance:** pure helpers have direct tests; the existing real-engine composition/navigation/paint-delivery
suite remains intact; GPU/software parity and representative live/attached/detached performance
evidence are no worse for the same workload and environment.

### Batch I -- test suite and desktop harness decomposition

You may split tests alongside their production batch, but do the large shared harness cleanup after
the relevant production interfaces stabilize.

For every split, record original test IDs and an explicit old→new mapping. Preserve assertions,
setup/teardown ordering, skip conditions and subprocess isolation. New tests may increase the count.
A net drop needs an individual reviewed reason; file count or coverage percentage alone does not
prove parity.

Never import a test case with executable methods into several newly discovered `test_*.py` modules:
unittest may execute it repeatedly. Put reusable fixtures and helpers in clearly named support
modules. Do not add `QGuiApplication` creation at import time.

The fixture's module lifecycle matters. Load plugin owners inside the owning runtime fixture. Do not
keep plugin classes/functions loaded at support-module import time and then patch a different module
instance later. Patch the name actually looked up by the caller after extraction.

#### Scenarios

The baseline `tests/harness/scenarios.py` contains **126 scenarios in one `SCENARIOS` list**. Split
them by existing group into a package with an explicit assembly point. Preserve:

```text
all 126 IDs, with no duplicates
scenario names and group membership
list order where used by the runner
step order and step payloads
budgets, reset/boot boundaries and probe bodies
scratch-path construction and platform-specific path handling
surface/scenario-map coverage
```

Do not rely on filesystem glob order to assemble the suite. Compare a normalized before/after
serialization of scenario definitions under the same controlled scratch environment. Paths can
legitimately differ between hosts; the semantic definitions must not.

#### Runner and driver

Separate environment/package preparation, process lifecycle, scenario dispatch, evidence/capture
collection and gate/reporting responsibilities from the large runner. The runner entry point should
orchestrate those owners while retaining the existing command-line behavior, exit codes, retries and
evidence artifacts.

Separate driver probe/interaction families while retaining one driver lifecycle and GUI-thread
interaction owner. Preserve `objectName` lookup, window-relative input coordinates, screenshot/video
behavior, timeouts, teardown and protocol acknowledgements.

Update Docker/package/copy rules so every new harness module reaches the actual runtime environment.
A unit test importing a module in the checkout is not proof that the staged driver can import it.

#### Source-contract tests

Retarget architectural assertions to the correct new owner. Do not concatenate the whole source tree
or broaden basename lookup until an assertion finds a convenient match somewhere. That would make the
check less meaningful.

Keep file-placement/import-boundary checks separate from behavior checks. Where a source-token test
must change after a legitimate extraction, explain the preserved contract and add direct
owner/composite tests when token matching no longer proves it.

**Acceptance:** all old executable tests and scenario semantics are accounted for; discovery is
non-vacuous; no duplicate Qt apps or tests appear; actual staged desktop harness imports and runs the
split files.

## Rules that are not negotiable

### A real extraction transfers ownership

A new module is justified when it owns a coherent policy, state machine, resource lifecycle or
visual component. It is not justified merely because the original file is long.

Do not use: a mixin hierarchy whose methods all mutate the same giant `self`; free functions that
accept the entire Monitor/coordinator as `self` in disguise; a shared context bag containing every
old field; `__getattr__`, dynamic method installation, wildcard imports or compatibility forwarding
files to hide unresolved ownership; a new generic event bus, dependency injection framework,
universal controller or global service locator; a replacement mega-module with the old code pasted
under a new name.

Pass explicit collaborators or capabilities. `parent=` for QObject lifetime is not permission to
inspect that parent's private application state.

Small private methods within a cohesive state owner are valid. A coherent 900-line owner is
preferable to nine circularly dependent 100-line fragments.

### Preserve

- Public `pyqtProperty`/signals/slots: names, types, defaults, return values, existing bindings. Do
  not migrate everything to a new nested public API at the same time.
- An extracted file loses access to IDs in the old document. Enumerate every such reference BEFORE
  moving it; replace each with a declared input, a narrow signal, or local ownership — never a covert
  `root`/`parent.parent`.
- Signal order, synchronous user-action publication, heartbeat coalescing, absence/sentinel handling,
  QVariant payload identity, notification groups. No "emit every signal on every update".
- A follower attachment/view change is visible before the corresponding plate payload change.
  Thumbnail completion must not rebuild the full Monitor publication. An unchanged chart, file-row
  projection or decoded layer must not become a newly allocated payload on every poll.
- Algorithms, threading models, poll cadence, cache format, checkpoint cadence, sleep policy,
  geometry format, shader source, colour semantics, UI behaviour. A necessary correctness fix gets
  its own commit and explanation.
- One owner per state/resource. No duplicate timers, subscriptions, QNetworkAccessManagers, caches
  or writers.
- No O(layers)/O(rows)/full-array copying/repeated QVariant conversion/per-motion Python↔QML
  crossings on hot paths.

Run tests against fixtures/simulators. **Never** send live movement, heating, print or delete
commands to a real printer.

### Never

- Loosen, skip, xfail or delete an assertion to get green. Retargeting a pin to a real new owner is
  fine; weakening is not.
- Change CI triggers, skip tests, lower coverage, add `[skip ci]`, or add blanket coverage
  exemptions / `pragma: no cover` / move code back into a covered file to dilute a gate.
- Use `reset --hard`, `clean -fd`, history rewriting, force-push, or delete another contributor's
  changes. A rejected non-fast-forward push is a request to reconcile, not permission to force.
- Touch `main`, release branches, tags, or the branches `work/v4.6.2-domain-ownership-c42cda7` and
  `work/v4.6.2-refactor-validation`. Never merge those histories or copy their workflows in.
- Bump the version, create a tag, publish a release, or upgrade Qt/Python/Cura/dependencies.
- Quote or attribute anyone in repo text; the phrase "the author" is banned.

## How to work

The host has **no PyQt6**. Everything runs in the pinned container. Linux defaults to Docker.

```sh
# focused modules — one process per file, parallel
./tools/docker_dev.sh sh -c "cd /work && JOBS=2 SHARDS=1 tools/run_some.sh tests.test_a tests.test_b"

# never: several files to one `python3 -m unittest` call (real-Qt files cannot share a process)
```

**`run_some.sh` globs `tests/test_*.py` and misses the four `tests/harness/test_harness_*.py`
legs.** Run those explicitly, or use the hook.

The full local gate — six legs, ~6 min, reads STAGED files, so `git add` first:

```sh
tools/pre_commit.sh     # must print: pre-commit: all checks passed
```

A commit blocked by the hook is a real signal. Fix the cause; never `--no-verify`.

Small coherent steps. Commit each substep, push it, read the CI run for that exact SHA. Do not make
two known-breaking commits such as "move files now, repair imports later". Do not accumulate several
completed extractions locally.

Before pushing: `git fetch origin && git rev-parse origin/chore/v4.6.2`. If the remote has advanced
past your base, stop and reconcile rather than forcing.

### CI

Normal CI takes 15–25 minutes and includes Lint, CodeQL, Screenshot sync, Build package, Python
3.10/3.11/3.12 with the real Qt runtime, both native builds, and a Cura repeat-boot smoke. Read the
run for your exact SHA:

```sh
gh run list --repo shallax/MoonrakerPrintFollower --branch chore/v4.6.2 --limit 5
gh run view RUN_ID --repo shallax/MoonrakerPrintFollower --log-failed
```

**A queued or running job is not a pass.** Two of the six legs' failures this refactor produced
showed *no* `FAIL:` block at all — see the landmines below.

## Landmines — each of these actually cost time on this branch

1. **A Qt `Q_ASSERT` abort is not a test failure.** Three CI jobs failed with no `FAIL` block and
   exit 1 because the interpreter aborted. A test double declared `@harness.pyqtSlot()` with no
   arguments against a model declaring `@pyqtSlot(bool)`. `Q_ASSERT` is compiled out of release Qt,
   so Windows and macOS ran the same broken call silently; only the Linux wheel aborts. **Test
   doubles must match production signatures exactly.**

2. **A moved document silently breaks `Qt.resolvedUrl` paths.** Moving QML one directory deeper
   broke icon references and two icons stopped painting — caught only by the pixel oracle, not by any
   test. `tests/test_resource_references.py` owns this check; prove a fix by deliberately breaking a
   reference and confirming it fails.

3. **`objectName`s are located by the desktop harness**, not by position. A splice can drop one.

4. **A declared import with no consumer is not necessarily an orphan.** `import QtQuick.Controls 2.15`
   is pinned as a declared SDK surface by `test_sdk_compatibility`. Removing "unused" imports fails a
   contract test.

5. **`patch.dict(sys.modules)` teardown deletes fixture-imported modules.** Never hold a plugin class
   across fixtures: the next fixture's import builds a *second* copy, the test patches the new one,
   the cached class uses the old one, and every spy counts zero. Import the owner inside the fixture
   that uses it.

6. **A quoted shell glob never expands.** `'mpf/**/*.qml'` reaches `qmlformat` as a literal filename.
   POSIX sh cannot express "any depth" — expand inside the script.

7. **`--diff-filter=ACM` reads a rename as no change at all.** The format gate was handed an empty
   file list and passed having checked nothing. It reads `ACMR` now.

8. **The hook runs its six legs concurrently** and its unit leg failed once under that load, then
   passed on immediate re-run with no tree change. Load-sensitive, not diagnosed.

9. **`tools/pre_commit.sh` runs the unit leg in the container** because the host has no PyQt6; a
   module-guarded QML test file discovers zero tests on the host and Python exits 5 (`NO TESTS
   RAN`). Running `LEGS=host tools/run_tests.sh` directly on the host gives a false failure.

10. **Coverage's `ctrace` core cannot see Python frames entered from Qt worker threads.**
    `PlateRenderController.py` measured 55 missed lines under `ctrace` and 1 under `sysmon`, with 54
    lines flipping one way and none the other, and 0 worse / 2 better across all 134 measured `mpf/`
    files at identical statement counts. CI's Python 3.12 defaults to `ctrace`
    (`SYSMON_DEFAULT` starts at 3.14); the workflow now sets `COVERAGE_CORE: sysmon` for that step.

11. **A raw `docker run` scratch harness does not resolve a worktree's `.git` pointer**, producing
    exit-128 package-timestamp failures. Use `tools/docker_dev.sh`, which mounts the real gitdir.

## Open defects — do not let these read as green

- **An unreproduced segmentation fault** on the `PlateQt` raster worker threads (`PlateQt._derive_grey`,
  `PlateQt.png_file`, via `sip_api_convert_to_enum`). Recorded in the ledger. An earlier write-up
  called it load sensitivity; **the artifact is a crash and that reading is not established.** If you
  reproduce it, report it — do not catch it away with a guard.

- **A macOS animation assertion** in
  `tests/test_gpu_canvas_isolation.py::test_live_layer_handoff_fades_previous_geometry_then_retires_it`
  failed once and passed on re-run. It asserts a QML animation advanced inside a fixed pump window.
  Load-sensitive; not fixed. A green that needs a second attempt is not a fixed test.

- **The pre-commit hook's unit leg** failed once under its own concurrent load and passed on re-run.
  Same class; not diagnosed.

## Process notes worth knowing

- The QML batches (B, C, D) were each proven by a **pixel oracle**: capture with the `tools/capture_*.py`
  route into a scratch dir, then `cmp` byte-for-byte against the committed `screenshots/`. All three
  got exact matches, and the oracle caught the two silently-unpainted icons. A green suite is not
  evidence that a QML move preserved rendering.
- Every batch found couplings its brief did not name — a `rowHeight` outside every enumerated member
  range, an `activeRows` consumed by two regions, a second writer of a camera nonce sitting 80 lines
  from the rest of its state. Expect the same; report them.
- Mutation-check your own ordering claims. Batch F moved a store after an emit and confirmed exactly
  three of five tests failed, then restored the tree. That is the standard.
- The ledger at `docs/refactor-progress.md` is updated as part of normal batches — not as separate
  documentation-only commits.
