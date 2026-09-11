---
name: new-feature
description: Plan and build a new MoonrakerPrintFollower release end-to-end — version chat, critic round, six-persona panel (plus a pro-user persona from 3.6.0), decisions log, implementation, snapshot loop, panel re-review, reconciliation, ship via PR. Use when the user proposes a new version or feature for this plugin ("let's do 3.6.0", "next release", "new feature").
---

# New feature / release workflow

The author's preferred build flow for a MoonrakerPrintFollower release.
INSTRUCTIONS.md owns repo mechanics; this skill owns the process. The
author is in the loop at every decision point when present; when away
(e.g. an overnight run), every judgement call lands in
`review/DECISIONS.md` for audit.

## Phase 0 — Plan (chat, don't code)

1. The author gives a version number; read that section of ROADMAP.md
   (create it if absent — Claude owns the roadmap) and chat about the
   feature: how they think it should look and function. Capture their
   EXACT words — verbatim quotes go into the ROADMAP section and into
   every persona brief. The personas critique the author's vision, not
   my paraphrase.
2. Validate their thinking — NOT rubber-stamping. Challenge, question
   and push back until there is a mutual understanding: surface
   trade-offs they may not have seen, argue the counter-case where the
   evidence points that way, and hold the line on their standing
   decisions (safety model, persistence, no scope creep). Present a
   recommendation with every challenge; never silently accept or
   silently override; never report a discussion as an accepted
   decision unless they actually accepted it.
3. Work on a `vX.Y.Z` branch — NEVER push to main directly, not even
   one-line fixes: every change to main lands through a PR (the 3.5.0
   lesson). No PR until the author explicitly asks; no tag/release
   without them.

## Phase 1 — Round-1 general critic

ONE read-only critic agent BEFORE any implementation: point it at the
ROADMAP section, ARCHITECTURE.md, INSTRUCTIONS.md, the verbatim author
quotes and the intended implementation. Mandate: criticism before code,
no holds barred; read-only (no code changes, no repo edits — findings
funnel back through me); severity-ordered findings, no cap, plus its
top priorities.

## Phase 2 — Discuss its findings with the author

Walk the round-1 findings together; the author weighs in on each;
agreed plan adjustments go back into the ROADMAP section. If the author
is away, apply cheap plan-level wins and log every call in
`review/DECISIONS.md`.

## Phase 3 — The panel

Six read-only agents IN PARALLEL for a release round: architecture, UX,
engineering, product, security/hardening, and the Klipper/Moonraker/Cura
domain expert — its protocol archaeology catches end-user bugs no other
lens can see (the 30 s timeout painted commands red; multi-extruder
filament read "0.00 m"), so keep its brief on load-bearing claims, not
exhaustive verification. From 3.6.0 a seventh joins: the 3D-printer
enthusiast/pro-user persona, on feature value for the NEXT release —
feeding planning, not gate-calls. Each brief contains, verbatim where
quoted:

- the agreed ROADMAP scope for the release
- the author's own words on how it should look and function
- what exists today and what is planned but unbuilt
- the round-1 findings relevant to that role
- rules: CRITIC ONLY — no code changes, nothing written into the repo,
  everything funnels back through me; research downloads (upstream
  sources, clones) go to a tmp location, NEVER into the source tree
  (the author's rule, 2026-09-10)
- findings: NO CAP — surface everything; then name their top 3
- websocket discipline: 4.0.0 IS the socket release — the swap stays
  the status feed only; new socket-driven features are tech-debt notes for
  later releases
- the UX persona holds explicit adjudication rights on the release's
  open UX question (the author defers to it) — but adjudication is a
  RECOMMENDATION: the author has final approval on every ruling,
  persona verdicts included, before anything is built on them

Keep the panel agents resumable — phase 6 re-engages the SAME personas
rather than fresh critics, so their re-review is against their own
recommendations.

## Delta reviews (the author's ruling, 2026-09-10)

At ANY step, if the work has drifted materially from what a persona
adjudicated — or the author's live rulings re-shaped an adjudication —
suggest a DELTA review: re-engage the SAME persona (the panel agents
are resumable for exactly this), constrained to what changed and
whether the drift breaks the intent of anything it or the author
previously ruled. Read-only; findings funnel back through me; the
author has final approval on everything, as always. Be proactive —
propose one whenever it would catch a contradiction while it is still
cheap, not after it is built.

## Phase 4 — Decision making

Consolidate the four reports into `review/round-<n>-<role>.md`
(git-ignored) and one row per finding in `review/DECISIONS.md`
(IMPLEMENT / DEFER / MODIFY + why). With the author present, decisions
are a discussion; without, make the pragmatic calls and log them all.
Rule: implement everything that is not in conflict — including optional
items; where findings conflict, pick the pragmatic side and push on.

## Phase 5 — Implement

Build feature-by-feature with tests as we go (pure domain tests first,
Qt tests, token pins). Follow the repo recipes in INSTRUCTIONS.md: one
owner per domain, value_property publishing, atomic state writes,
surface lists in tests/test_composed_components.py, section pins.
Update ARCHITECTURE.md and its contract tests in the same commits as
the code they describe.

**Snapshot sequencing (the author's ruling, 2026-09-10):** large
surfaces ship as author-testable snapshots, ordered by taste-value, so
the author tries things early instead of discovering everything at the
end of a long session:

- **Snapshot 0 — mock-up before wiring**, for any large new UI: the
  real QML with static synthetic data, no network, no model — packaged
  through the snapshot loop so the author judges shape, columns and
  layout at real size before any machinery exists. Their nod gates the
  functional work.
- Then the functional slices: the READ-ONLY slice first (everything
  visible, no mutations), the marquee verb second, the mutations last;
  independent features ride a separate track so regressions stay
  attributable.
- Each snapshot ends with the author's live test
  (`make snapshot_package` → SCP → try it); the next slice waits for
  their nod. Plan the snapshot order at the decision walk, not at
  implementation time.
- Tight iteration loops use `make snapshot_quick` (format + QML
  sanity + verified package — no full test suite, no captures); the
  author's ruling (2026-09-10): during focused iteration a valid
  build that at least runs beats waiting for a full gate. `make all`
  stays mandatory before any commit or push.

- Every fix lands WITH ITS PROOF: the test, grep or output that shows
  it works, named in the report. "Done" without evidence is not done.
- Test pins are written against the actual file content, never a
  mental model of it.
- Aesthetic/UI changes: implement the MINIMAL version first — the
  author tests taste fast, and the full-bore version waits for their
  nod (the console-colour double-round).
- Workflow/CI/release changes are NOT verified by the local gates:
  rehearse them (`act`, or a dry-run of the step in the container)
  before calling them fixed. The panel's scope includes the release
  path itself, not just product code.

## Phase 6 — Panel re-review

Re-engage the SAME four personas over the finished build: their own
recommendations are the checklist — did the implementation actually do
what they asked? Disagreements are settled here: the author arbitrates
when present; otherwise the pragmatic call goes into DECISIONS.md.

## Phase 6b — Adversarial round

A FRESH critic — not the panel, not the round-1 critic — attacks the
finished build: new defects, regressions introduced by the fixes, and
blind spots every earlier round shared. The panel re-review verifies
the plan was followed; this round verifies the plan was *right*. Both
feed phase 7.

## Phase 7 — Reconciliation

Apply the re-review's fixes; record the final accept/defer/modify state
per finding in DECISIONS.md; mark the ROADMAP section shipped.

## Ship, then the usual push and check/fix cycle

- Docs: CHANGELOG section, README release header + "What changed",
  INSTRUCTIONS if procedures changed, ARCHITECTURE if boundaries changed.
- Version bump per INSTRUCTIONS' checklist (package.json, plugin.json,
  CHANGELOG, README, git tag — the tag only when the author releases).
- Screenshots regenerated in the pinned container (canonical fonts);
  captures must show the new UI, including empty states and synthetic
  data where the real feed cannot be captured.
- `make all` green before every push.
- THE SNAPSHOT LOOP (the author's invention, their favourite part of
  the flow): `make snapshot_package` on demand — from the working
  tree, BEFORE committing ("snapshot me!") — the author SCPs
  `/tmp/mpf.curapackage` and live-tests; commits and pushes HOLD until
  the author is happy with what they tested. It keeps the git log
  clean and skips waiting on GitHub builds.
- Release flow: PR the branch into main (never push main directly),
  and tag `vX.Y.Z` on main only when the author says release — the
  release workflow builds, validates and publishes the artifacts.
- When the author asks for a compacted history, rebuild with boundary
  trees and verify the result byte-for-byte against a stashed
  reference tree before force-pushing.

## Standing rules (from memory)

- NO AI ATTRIBUTION ANYWHERE: no Co-Authored-By trailers, no
  "Generated with Claude Code" footers — not in commits, PR bodies,
  changelogs, docs, releases, anything ("never, ever to put a
  'Generated with AI' thing anywhere"). Commit messages stay plain
  and factual, no AI-voice framing.
- Repeated command sequences become make targets only when genuinely
  beneficial to other maintainers.
- Real-Cura capture automation is permanently rejected — hand-capture
  3-D shots.
- The ROADMAP is the living spec and Claude owns it.
- Persona adjudication is advisory — the author has final approval on
  every ruling, persona verdicts included (2026-09-10).
- Research downloads (upstream sources, clones, doc fetches) never
  land in the source tree — always a tmp location (2026-09-10).
- ALL temp files — probes, scratch QML, transient outputs — live in
  the deterministic scratch dir `/tmp/mpf` (2026-09-10): it keeps
  things cleanable and the dev container bind-mounts it, so probes
  run in-container directly. Nothing transient in the source tree.
- Test pins update IN THE SAME PASS as the change (2026-09-10):
  grep the tests for the changed token (QML pins, composed surface
  lists, allowlists, the no-reflow sweep) and rewrite the pin
  immediately — never leave them for the gates to discover.
- Combine the run with the error extraction (2026-09-10): one
  command for the gate/build AND the verdict + failure names +
  assertion details (grep FAIL:/AssertionError inline in the same
  tool result) — never `tail -3` first and go looking for errors
  afterwards.
- Debugging/probing QML from Python (2026-09-10): QML `id`s never
  cross the C++ boundary — probe scripts and Qt tests address
  controls by `objectName`. Key interactive items carry objectName,
  and probe scripts print names, never class types.
