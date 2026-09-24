"""The coordinator's refresh throttle (the 4.6.0 responsiveness pass).

Every task boundary of the background preparation pass emitted
``changed``, and the coordinator answered each one with a full refresh:
the plate payload, the physical-layer resolution and the printed
objects' walk. A pass that runs eight batches a second therefore bought
eight walks a second for a progress bar the snapshot already carries.

The contract these tests hold:

* the pass's per-batch ticks are progress, and progress rides its own
  throttled signal — the coordinator's refresh count over a whole pass
  follows the transitions it published, not the batch count;
* the transitions the UI latches on keep publishing: the phase change
  into and out of the build, the pass's completion, and an error;
* a tick carries the live load term, because the Monitor's improve-Eta
  hourglass ends on a rebuilt snapshot whose load term is False.

The assertions are counts, never clocks. A refresh is a call and a
transition is an emission, so the same numbers read on an idle runner
and on one running the whole suite at once — the batch COUNT is the
only machine-dependent term, and a faster machine taking fewer batches
weakens the evidence without touching the contract.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from types import SimpleNamespace

from tests.qt_runtime_support import QT_AVAILABLE, runtime

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication

    from plugins.PrinterConfig import PrinterConfig
    from tests.test_coordinator_toolhead_console_coverage import (
        _BedMesh,
        _Binding,
        _Client,
        _Cura,
        _Files,
        _Pauses,
        _Presentation,
        _Preview,
        _coordinator_class,
    )
    from tests.test_plate_progress import make_index


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class CoordinatorRefreshThrottleTests(unittest.TestCase):
    """A real index service running a real pass, wired to the real
    coordinator: the per-batch ticks are the service's own, so a fake
    emitter could not show what the throttle removes."""

    # Dense enough that the pass needs several of its 120 ms batches.
    LAYERS, MOTIONS = 3000, 100

    class _Identity:
        uuid = "u"
        modified = 1.0
        size = 100

        def stable_key(self):
            return "print-key"

    def setUp(self):
        self._rt = runtime()
        self.fixture = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _compose(self):
        from plugins.PreparedStore import PreparedCache
        module = self.fixture.load("GCodeIndexService")
        parts = SimpleNamespace(
            client=_Client(), binding=_Binding(PrinterConfig()),
            files=_Files(), cura=_Cura(), preview=_Preview(),
            pauses=_Pauses(), presentation=_Presentation(), bed_mesh=_BedMesh())
        parts.files.identity = self._Identity()
        parts.files.job_key = ("part.gcode", 100, 1)
        parts.index = module.GCodeIndexService(
            parts.files, object(), prepared=PreparedCache(self.dir.name))
        parts.coordinator = _coordinator_class()(
            client=parts.client, binding=parts.binding, files=parts.files,
            index=parts.index, cura=parts.cura, preview=parts.preview,
            pauses=parts.pauses, presentation=parts.presentation,
            bed_mesh=parts.bed_mesh)
        self.addCleanup(parts.coordinator.close)
        self.addCleanup(parts.index.close)
        return parts

    def _watch(self, parts):
        """Observe every task boundary the pass reaches, whichever
        signal carries it, and route the coordinator's refreshes
        through a counter.

        The transition count is the number of DISTINCT states those
        boundaries land in — phase, pass completion, coverage
        completion. It is measured from the service's own public state,
        so it does not depend on which signal the implementation chose
        to publish that boundary on: the count is the same before and
        after the throttle, and only the refresh count moves.
        """
        index, coordinator = parts.index, parts.coordinator
        state = SimpleNamespace(
            boundaries=0, states=set(), changes=[], progress=[], refreshes=[],
            kinds=[], published=lambda: len(parts.presentation.published))

        def snapshot():
            return (index.phase, index._prepared_saved, index._prepared_complete)

        def observe():
            state.boundaries += 1
            state.states.add(snapshot())

        index.changed.connect(observe)
        index.changed.connect(lambda: state.changes.append(snapshot()))
        progress_signal = getattr(index, "progress_changed", None)
        if progress_signal is not None:
            progress_signal.connect(observe)
            progress_signal.connect(lambda: state.progress.append(1))
        # The tasks the service ran, one entry per submission. It is
        # the seam BOTH sides of the contract read: the batches the pass
        # needed, and — a task publishing on each side of its worker —
        # the number of boundaries that are not the pass's own.
        submitted = index._submit

        def counted_submit(kind, work, lease=None):
            state.kinds.append(kind)
            return submitted(kind, work, lease)

        index._submit = counted_submit
        original = coordinator.refresh

        def counted():
            state.refreshes.append(1)
            return original()

        coordinator.refresh = counted
        return state

    def _settle(self, parts, timeout=30.0):
        """Drive the pass the way the poll does: _advance submits, and
        the worker's terminal rides a QUEUED signal, so the loop must
        process events rather than sleep."""
        index = parts.index
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            index._advance()
            if index._prepared_saved and not index._busy:
                return
            QCoreApplication.processEvents()
            time.sleep(0.002)
        self.fail("the prepared pass did not settle: busy=%r error=%r "
                  "frontier=%s/%s phase=%s"
                  % (index._busy, index._error, index._full_next,
                     len(index._view.ranges) if index._view else None, index.phase))

    def _run_pass(self, parts):
        index = parts.index
        index.bind(parts.files.job_key)
        # The pass is the subject, not the store's restore: the view is
        # installed directly so every batch walked is a batch emitted.
        index._restored = True
        index._wanted = True
        index._view = self.fixture.load("GCodeIndexService").IndexView(
            parts.files.job_key, make_index(layers=self.LAYERS, motions=self.MOTIONS))

    def test_a_pass_refreshes_once_per_transition_and_never_per_batch(self):
        # The pin: the coordinator's refresh count over a whole pass
        # follows the transitions the pass published, not the batch
        # count. Every refresh bought for a batch is a plate payload and
        # a printed-object walk spent on a progress tick.
        parts = self._compose()
        state = self._watch(parts)
        self._run_pass(parts)
        self._settle(parts)
        index = parts.index
        self.assertTrue(index._prepared_saved, "the pass never completed")
        batches = [kind for kind in state.kinds if kind == "fullprep"]
        self.assertGreater(len(batches), 4,
                           "the pass produced too few batches to be evidence")
        # Both halves of the contract. Every transition the pass
        # reached must have had its refresh — a dropped completion or
        # error is a user-visible regression.
        self.assertGreaterEqual(
            len(state.refreshes), len(state.states),
            "the coordinator ran %d refreshes for %d transitions %s — one "
            "went without" % (len(state.refreshes), len(state.states),
                              sorted(state.states)))
        # And the batches must not each buy one: a task publishes on
        # both sides of its worker, so a per-batch refresh would sit at
        # twice the batch count. The ceiling is the boundaries that are
        # NOT the pass's batches (each of those publishes a change),
        # plus the one batch boundary that may itself move the state —
        # the first batch of a newly bound job — plus bind()'s own
        # publish. Neither of the last two grows with the batch count.
        others = 2 * (len(state.kinds) - len(batches))
        self.assertLessEqual(
            len(state.refreshes), others + 2,
            "the coordinator ran %d refreshes over %d pass-batch boundaries "
            "(%d batches) and about %d other boundaries (%d transitions %s) — "
            "the batches are still buying refreshes"
            % (len(state.refreshes), 2 * len(batches), len(batches), others,
               len(state.states), sorted(state.states)))
        self.assertGreaterEqual(len(state.progress), 1,
                                "no tick rode the dedicated progress signal")
        self.assertGreaterEqual(
            state.published(), len(state.refreshes) + len(state.progress),
            "a progress tick published nothing — the bar would freeze")

    def test_a_pass_tick_carries_the_live_load_term(self):
        # The Monitor's improve-Eta hourglass ends on a rebuilt snapshot
        # whose load term is False (the model's identity gate), so a
        # tick that republished the last refresh's stale copy would end
        # an hourglass whose load is still running.
        parts = self._compose()
        state = self._watch(parts)
        index, coordinator = parts.index, parts.coordinator
        armed, ticks = [], []

        def record():
            if armed:
                ticks.append((len(state.refreshes), coordinator.snapshot.load_active))

        def arm():
            if armed:
                return
            armed.append(len(state.refreshes))
            # The improve click's tracker effect (download_for_monitor's
            # first act), taken directly so the refresh the click delays
            # by 2.6 s cannot stand in for the tick.
            coordinator._loads.request_monitor()

        index.progress_changed.connect(record)
        index.progress_changed.connect(arm)
        self.assertFalse(coordinator.snapshot.load_active,
                         "the fixture starts with the load term set — the pin "
                         "could not tell the live term from the copy")
        self._run_pass(parts)
        self._settle(parts)
        self.assertTrue(armed, "the pass emitted no tick to arm on")
        # A refresh after the arm would itself set the load term, so
        # only a tick that landed before one answers the question — and
        # with no such tick the pin must fail, not pass.
        live = [flag for stamp, flag in ticks if stamp == armed[0]]
        self.assertTrue(live, "no tick landed between the Improve-Eta request "
                              "and the coordinator's next refresh")
        self.assertTrue(live[0],
                        "the first tick after the Improve-Eta request "
                        "republished a stale load term")

    def test_the_pass_completion_keeps_its_transition(self):
        # The risk the plan names: completion is an edge the UI latches
        # on, so the emit that carries it must not be throttled away.
        parts = self._compose()
        state = self._watch(parts)
        self._run_pass(parts)
        self._settle(parts)
        self.assertTrue(state.changes[-1][1],
                        "the pass's completion was never published as a change")

    def test_the_error_transition_keeps_its_full_refresh(self):
        # The other half of the same risk: a build that fails must
        # still reach the coordinator as a change, or the card keeps
        # rendering a load that will never arrive.
        parts = self._compose()
        state = self._watch(parts)
        index = parts.index
        index._submit("build", lambda: None)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and index.phase != "error":
            QCoreApplication.processEvents()
            time.sleep(0.002)
        self.assertEqual(index.phase, "error", "the failed build left the indexing phase")
        self.assertIn("error", [phase for phase, _saved, _done in state.changes],
                      "the error transition was never published as a change")
        self.assertEqual(len(state.refreshes), len(state.states),
                         "the error transition did not get its full refresh")


if __name__ == "__main__":
    unittest.main()
