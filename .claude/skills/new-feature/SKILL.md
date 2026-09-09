---
name: new-feature
description: Plan and build a new MoonrakerPrintFollower release end-to-end — version chat, critic round, four-persona panel, decisions log, implementation, panel re-review, reconciliation, ship. Use when the user proposes a new version or feature for this plugin ("let's do 3.6.0", "next release", "new feature").
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
3. Work on a `vX.Y.Z` branch — never main. No PR until the author
   explicitly asks; no tag/release without them.

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

Four specialist agents IN PARALLEL: architecture, UX, engineering,
product. Each brief contains, verbatim where quoted:

- the agreed ROADMAP scope for the release
- the author's own words on how it should look and function
- what exists today and what is planned but unbuilt
- the round-1 findings relevant to that role
- rules: CRITIC ONLY — no code changes, nothing written into the repo,
  everything funnels back through me
- findings: NO CAP — surface everything; then name their top 3
- websocket discipline: socket improvements are tech-debt notes for
  ROADMAP 4.0.0, never scope creep into the current release
- the UX persona holds explicit adjudication rights on the release's
  open UX question (the author defers to it)

Keep the panel agents resumable — phase 6 re-engages the SAME personas
rather than fresh critics, so their re-review is against their own
recommendations.

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
- `make all` green before every push. Push to the `vX.Y.Z` branch only,
  then the usual push → CI → fix cycle. No PR unless the author asks;
  no tag/release without the author.

## Standing rules (from memory)

- Commit messages: no AI-voice framing, no Co-Authored-By trailers.
- Repeated command sequences become make targets only when genuinely
  beneficial to other maintainers.
- Real-Cura capture automation is permanently rejected — hand-capture
  3-D shots.
- The ROADMAP is the living spec and Claude owns it.
