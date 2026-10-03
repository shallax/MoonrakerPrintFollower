"""The what's-new content and its once-per-version marker contract.

The content is hand-curated per release; these pins make the curation
checkable — a version bump in package.json without a matching WHATS_NEW
head fails here, and the harness's seeded marker stays pinned to the
shipped version so the suite's runs never see the popup unless a
scenario asks.

The offer's window-side mechanics are pinned at the end against the
harness's real engine: the detection offer mounts the shipped document,
and the paths that only a live host reaches (a broken document, an
unreachable engine, a closed window) fail there or nowhere.
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from mpf.whatsnew.WhatsNew import WHATS_NEW, entries, latest_version, should_show
from tests import qml_engine_support as harness
from tests.qt_runtime_support import QT_AVAILABLE, runtime
from tests.source_root import SourceRoot

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = SourceRoot(ROOT / "mpf")

if QT_AVAILABLE:
    from PyQt6 import sip
    from PyQt6.QtCore import QEvent, QObject, pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtQuick import QQuickWindow

    class RecordingTimer:
        """The overlay's deferred work, captured instead of armed."""

        def __init__(self):
            self.calls = []

        def singleShot(self, delay, callback):
            self.calls.append((delay, callback))

    class DetectionDouble(QObject):
        """The service's QML surface: what the offer document reads."""

        stateChanged = pyqtSignal()

        def __init__(self, should_offer=True):
            super().__init__()
            self._should_offer = should_offer
            self.setups = 0
            self.cancels = 0
            self.declines = 0

        @pyqtProperty(bool, notify=stateChanged)
        def should_offer(self):
            return self._should_offer

        @pyqtProperty(bool, notify=stateChanged)
        def busy(self):
            return False

        @pyqtProperty(bool, notify=stateChanged)
        def ready(self):
            return False

        @pyqtProperty(str, notify=stateChanged)
        def phase(self):
            return "checking"

        @pyqtProperty(int, notify=stateChanged)
        def received(self):
            return 0

        @pyqtProperty(int, notify=stateChanged)
        def total(self):
            return 0

        @pyqtProperty(str, notify=stateChanged)
        def error(self):
            return ""

        @pyqtProperty(int, constant=True)
        def runtime_size(self):
            return 0

        @pyqtSlot()
        def setup(self):
            self.setups += 1

        @pyqtSlot()
        def cancel(self):
            self.cancels += 1

        @pyqtSlot()
        def decline_offer(self):
            self.declines += 1

    class MonitorDouble(QObject):
        """The monitor's what's-new surface, following the shipped one."""

        whatsNewRequested = pyqtSignal()
        whatsNewDismissed = pyqtSignal()

        def __init__(self, seen=""):
            super().__init__()
            self.checks = 0
            self._whats_new_seen = seen

        def checkWhatsNew(self):
            self.checks += 1

    class PopupDouble:
        """A popup as close() meets one: alive, or already gone."""

        def __init__(self, alive=True):
            self.deletions = 0
            self._alive = alive

        def deleteLater(self):
            self.deletions += 1
            if not self._alive:
                raise RuntimeError("wrapped C/C++ object has been deleted")


class WhatsNewContentTests(unittest.TestCase):
    def test_shipped_release_notes_are_frozen(self):
        # Once a release's notes are written, they are FROZEN — a
        # later release adds its own entry, never edits the older
        # ones (the ruling). The pin covers every entry
        # except the head (the release in development); shipping a
        # new release moves the old head into the frozen set and
        # recomputes this pin in the same pass (the version bump
        # checklist).
        import hashlib

        historical = [
            {"version": entry["version"], "headline": entry["headline"],
             "items": list(entry["items"])}
            for entry in WHATS_NEW[1:]
        ]
        payload = json.dumps(historical, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        self.assertEqual(
            digest,
            "eb0205cd97271930536e0484da35fe7468fbe2a388d42b67f3960ed888aee915",
            "the historical what's-new content changed — shipped release "
            "notes are frozen; only a new head entry may be added, and "
            "this pin recomputed for the release")

    def test_the_content_reads_like_release_notes(self):
        # The ruling: the popup's content is hand-curated and
        # user-facing — the maintainer-level detail stays in
        # CHANGELOG.md. No markdown survives into the rendered text.
        for entry in WHATS_NEW:
            for item in (entry["headline"], *entry["items"]):
                self.assertNotIn("`", item, entry["version"])
                self.assertNotIn("**", item, entry["version"])
    def test_the_latest_entry_is_the_shipped_package_version(self):
        # The release checklist adds a WHATS_NEW head alongside the
        # version bump — this pin fails a release that does one
        # without the other.
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(latest_version(), package["package_version"])
        self.assertEqual(latest_version(), WHATS_NEW[0]["version"])

    def test_should_show_gates_on_the_marker(self):
        # A fresh install stores nothing — the gate shows. Any stored
        # marker that is not the shipped version also shows; only the
        # shipped version itself stays quiet.
        self.assertTrue(should_show(""))
        self.assertTrue(should_show(None))
        self.assertTrue(should_show("4.0.2"))
        self.assertFalse(should_show(latest_version()))

    def test_entries_flag_only_the_latest(self):
        content = entries()
        self.assertTrue(content[0]["isLatest"])
        self.assertFalse(any(entry["isLatest"] for entry in content[1:]))

    def test_entries_preserve_every_curated_item_in_order(self):
        content = entries()
        self.assertEqual([entry["version"] for entry in content],
                         [entry["version"] for entry in WHATS_NEW])
        for entry, curated in zip(content, WHATS_NEW, strict=True):
            self.assertEqual(entry["items"], list(curated["items"]))
            self.assertEqual(entry["headline"], curated["headline"])

    def test_every_release_has_curated_items(self):
        for entry in WHATS_NEW:
            self.assertTrue(entry["version"])
            self.assertTrue(entry["headline"])
            self.assertTrue(entry["items"])
            self.assertTrue(all(entry["items"]))
        self.assertEqual(len({entry["version"] for entry in WHATS_NEW}),
                         len(WHATS_NEW))

    def test_the_overlay_sources_are_present(self):
        # The overlay's QML ships beside the content module; the
        # extension loads it at offer time, so a dropped file would
        # fail at runtime, not here — this pins the packaging half.
        self.assertTrue((PLUGINS / "WhatsNewOverlay.qml").is_file())


class WhatsNewSeedTests(unittest.TestCase):
    def test_the_harness_seed_marker_suppresses_the_suite_popup(self):
        # The suite's seeded profile carries the marker so its runs
        # never see the popup unless a scenario (z16) clears it —
        # this pin keeps the seed on the shipped version.
        # The 4.5.0 fixture: the marker lives in the state document
        # under the plugin's one persistence folder now, not the
        # pre-4.5.0 sections file.
        seed = json.loads(
            (ROOT / "tests/harness/config/config/cura/5.13"
             / "MoonrakerPrintFollower" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(seed["whatsNewSeen"], latest_version())
        self.assertFalse(should_show(seed["whatsNewSeen"]))


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class WhatsNewDetectionOfferTests(harness.RealEngineTestCase):
    """The detection offer as the live run mounts it: the shipped
    document, Cura's engine, and a window that can go away."""

    def setUp(self):
        super().setUp()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.qt = stack.enter_context(runtime())
        self.module = self.qt.load("WhatsNewOverlay")
        self.logger = sys.modules["UM.Logger"].Logger
        # Every deferred callback is recorded, never armed: the boot
        # offer must not fire into a test that is mid-assertion.
        self.timer_records = RecordingTimer()
        stack.enter_context(patch.object(self.module, "QTimer", self.timer_records))
        # Cura's own stored engine is the handle the offer reaches for;
        # the harness's engine stands in for it.
        application = SimpleNamespace(getInstance=lambda: SimpleNamespace(_qml_engine=self.engine))
        package = ModuleType("UM.Qt")
        package.__path__ = []
        module = ModuleType("UM.Qt.QtApplication")
        module.QtApplication = application
        stack.enter_context(patch.dict(sys.modules, {
            "UM.Qt": package, "UM.Qt.QtApplication": module}))

    def overlay(self, detection=None):
        result = self.module.WhatsNewOverlay(detection=detection)
        self.addCleanup(result.close)
        return result

    def window(self, visible=True):
        window = QQuickWindow()
        window.resize(900, 700)
        if visible:
            window.show()
        self.addCleanup(self._close_window, window)
        self.pump(10)
        return window

    def _close_window(self, window):
        window.close()
        window.deleteLater()
        # A window that still exists is still a candidate for the offer,
        # so the teardown destroys it rather than merely hiding it.
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.pump(10)

    def install_monitor(self, monitor):
        patcher = patch.object(self.module.WhatsNewOverlay, "_find_monitor",
                               lambda _self: monitor)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_detection_offer_mounts_the_shipped_document(self):
        window = self.window()
        detection = DetectionDouble()
        offer = self.overlay(detection)
        offer._show_detection()
        popup = offer._detection_overlay
        self.assertIsNotNone(popup)
        self.assertEqual(popup.property("objectName"), "detectionFirstRunOffer")
        self.assertTrue(popup.property("visible"))
        self.assertIs(popup.property("detection"), detection)
        self.assertIs(popup.property("parent"), window.contentItem())
        self.assertEqual(popup.property("x"), max(0, round(
            (window.width() - popup.property("width")) / 2)))
        self.assertEqual(popup.property("y"), max(0, round(
            (window.height() - popup.property("height")) / 2)))

    def test_the_detection_offer_waits_for_a_visible_window(self):
        self.window(visible=False)
        offer = self.overlay(DetectionDouble())
        offer._show_detection()
        self.assertIsNone(offer._detection_overlay)

    def test_the_detection_offer_mounts_once_and_only_for_a_standing_offer(self):
        self.window()
        offer = self.overlay(DetectionDouble())
        offer._show_detection()
        first = offer._detection_overlay
        self.assertIsNotNone(first)
        offer._show_detection()
        self.assertIs(first, offer._detection_overlay)
        declined = self.overlay(DetectionDouble(should_offer=False))
        declined._show_detection()
        self.assertIsNone(declined._detection_overlay)

    def test_the_detection_offer_reports_a_document_that_fails_to_load(self):
        self.window()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        broken = os.path.join(directory.name, "DetectionOffer.qml")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("import QtQuick 2.15\nItem { this is not qml }\n")
        patcher = patch.object(self.module, "plugin_path", lambda *_args: broken)
        patcher.start()
        self.addCleanup(patcher.stop)
        offer = self.overlay(DetectionDouble())
        offer._show_detection()
        self.assertIsNone(offer._detection_overlay)
        self.assertEqual(self.logger.log.call_args[0][0], "e")
        self.assertIn("failed to load", self.logger.log.call_args[0][1])

    def test_the_detection_offer_survives_an_unreachable_engine(self):
        self.window()

        def explode():
            raise RuntimeError("no Cura application")

        patcher = patch.object(sys.modules["UM.Qt.QtApplication"], "QtApplication",
                               SimpleNamespace(getInstance=explode))
        patcher.start()
        self.addCleanup(patcher.stop)
        offer = self.overlay(DetectionDouble())
        offer._show_detection()
        self.assertIsNone(offer._detection_overlay)
        self.assertEqual(self.logger.log.call_args[0][0], "e")
        self.assertIn("detection offer raised", self.logger.log.call_args[0][1])

    def test_the_offer_hands_the_dismissal_to_the_detection_offer(self):
        self.window()
        monitor = MonitorDouble(seen="")
        offer = self.overlay(DetectionDouble())
        self.install_monitor(monitor)
        offer._offer()
        self.assertEqual(monitor.checks, 1)
        self.assertEqual([delay for delay, _ in self.timer_records.calls if delay == 0], [])
        monitor.whatsNewDismissed.emit()
        self.assertIsNotNone(offer._detection_overlay)
        # Without a service there is nothing for the dismissal to reveal.
        unwired = self.overlay()
        unwired._offer()
        self.assertEqual(monitor.receivers(monitor.whatsNewDismissed), 1)

    def test_seen_notes_queue_the_detection_offer_behind_the_check(self):
        self.window()
        monitor = MonitorDouble(seen=latest_version())
        offer = self.overlay(DetectionDouble())
        self.install_monitor(monitor)
        offer._offer()
        self.assertEqual(monitor.checks, 1)
        queued = [callback for delay, callback in self.timer_records.calls if delay == 0]
        self.assertEqual(queued, [offer._show_detection])
        queued[0]()
        self.assertIsNotNone(offer._detection_overlay)

    def test_a_reinstalled_monitor_is_rewired_and_the_deposed_one_dropped(self):
        # The machine-switch shape the plugin hands over: the offer must
        # ride the LIVE model, and a cached monitor's stale dismissal
        # must never reveal the offer for the new owner.
        self.window()
        first = MonitorDouble()
        offer = self.overlay(DetectionDouble())
        offer.attach_model(first)
        self.assertEqual(offer.offer_state()["wired"], True)
        second = MonitorDouble()
        offer.attach_model(second)
        self.assertEqual(first.receivers(first.whatsNewDismissed), 0)
        first.whatsNewDismissed.emit()
        self.assertIsNone(offer._detection_overlay)
        second.whatsNewDismissed.emit()
        self.assertIsNotNone(offer._detection_overlay)
        # Re-applying the same model changes nothing.
        offer.attach_model(second)
        self.assertEqual(second.receivers(second.whatsNewDismissed), 1)

    def test_an_offer_model_whose_notes_were_seen_queues_only_the_offer(self):
        self.window()
        monitor = MonitorDouble(seen=latest_version())
        offer = self.overlay(DetectionDouble())
        offer.attach_model(monitor)
        self.assertEqual(monitor.checks, 0)
        queued = [callback for delay, callback in self.timer_records.calls if delay == 0]
        self.assertEqual(queued, [offer._show_detection])

    def test_close_unwires_the_model_and_the_give_up_is_observable(self):
        monitor = MonitorDouble()
        offer = self.overlay(DetectionDouble())
        offer.attach_model(monitor)
        offer.close()
        self.assertEqual(monitor.receivers(monitor.whatsNewDismissed), 0)
        self.assertEqual(offer.offer_state()["wired"], False)
        # The retry loop's bound: a boot that never produces a window
        # stops retrying and SAYS SO, so a first-install leg can tell
        # never-wired from timed-out.
        self.install_monitor(None)
        silent = self.overlay(DetectionDouble())
        silent._attempts = 300
        silent._offer()
        state = silent.offer_state()
        self.assertEqual((state["wired"], state["gave_up"]), (False, True))

    def test_close_destroys_a_live_detection_offer(self):
        self.window()
        offer = self.overlay(DetectionDouble())
        offer._show_detection()
        popup = offer._detection_overlay
        self.assertIsNotNone(popup)
        offer.close()
        self.assertIsNone(offer._detection_overlay)
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.pump(5)
        self.assertTrue(sip.isdeleted(popup))

    def test_close_survives_a_whats_new_popup_that_is_already_gone(self):
        # The what's-new popup's Qt core can be destroyed first (Cura
        # owns its window); the detection offer's teardown must still
        # happen rather than be aborted by the dead one.
        offer = self.overlay()
        dead = PopupDouble(alive=False)
        live = PopupDouble()
        offer._overlay = dead
        offer._detection_overlay = live
        offer.close()
        self.assertEqual(dead.deletions, 1)
        self.assertEqual(live.deletions, 1)
        self.assertIsNone(offer._overlay)
        self.assertIsNone(offer._detection_overlay)


if __name__ == "__main__":
    unittest.main()
