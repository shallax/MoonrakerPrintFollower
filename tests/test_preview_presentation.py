"""Behavioural tests for the Cura/QML presentation adapter.

PreviewPresentation is the seam between Cura's QML card and the plugin's
user intents: it hosts one card inside Cura's action panel and one in the
corner overlay, decides which of the two is on, pushes the presenter's
values onto the live card, and turns the card's own signals into plugin
intents. The doubles here are real QQuickItems — parenting, child lookup,
the destroyed signal and deleteLater are genuine Qt — and the card is a
real item carrying the QML signal surface, so the gates, the value push
and the wiring are asserted as behaviour rather than as source text.

Every line of the module runs under this suite; nothing is left uncovered.
The approximation is on the engine side: Cura builds the hosts
(createQmlComponent) from its own QML modules, which this container does
not carry, so the items handed back here are built in-process and mirror
the two host documents. Loading the real QML files on the engine is the
release harness's job (tools/check_qml_engine.py, tools/capture_preview.py).
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

from qt_runtime_support import QT_AVAILABLE, runtime

PANEL_HOST = "MoonrakerPreviewCardPanelHost.qml"
OVERLAY_HOST = "MoonrakerPreviewCardOverlayHost.qml"
CARD_NAME = "moonrakerPreviewCard"
NO_CARD = object()

if QT_AVAILABLE:
    from PyQt6 import sip
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtQuick import QQuickItem

    class Card(QQuickItem):
        """The card's QML signal surface, which _wire looks up by name."""

        loadClicked = pyqtSignal()
        pauseClicked = pyqtSignal()
        improveEtaRequested = pyqtSignal()
        pauseAtLayerRequested = pyqtSignal(int)
        printPauseRequested = pyqtSignal()
        removePauseAtLayerRequested = pyqtSignal(int)
        clearPauseAtLayersRequested = pyqtSignal()
        bedMeshVisibilityRequested = pyqtSignal(bool)
        bedMeshThresholdsRequested = pyqtSignal(float, float)
        bedMeshExaggerationRequested = pyqtSignal(float)

        def __init__(self, *args):
            super().__init__(*args)
            self.destroy_calls = 0

        def deleteLater(self):
            self.destroy_calls += 1
            super().deleteLater()

    class OldCard(QQuickItem):
        """A card from a build that predates the rest of the deck."""

        loadClicked = pyqtSignal()

    class DeletedCard(Card):
        """A card whose C++ object Cura freed underneath us."""

        def setProperty(self, name, value):
            raise RuntimeError(f"{name}: wrapped C/C++ object of type QQuickItem was deleted")

    class DeletedHost(QQuickItem):
        """A host whose C++ object Cura freed underneath us."""

        def deleteLater(self):
            raise RuntimeError("wrapped C/C++ object of type QQuickItem was deleted")

    class BlindItem:
        """An item Qt refuses to read a property from: every lookup raises."""

        def __getattr__(self, name):
            raise RuntimeError("wrapped C/C++ object of type QQuickItem was deleted")

    class Host(QQuickItem):
        """The QML host document: an item with the card inside it.

        findChildren is the harness seam: the list a test hands in stands
        in for the child walk when the children are not items at all.
        """

        def __init__(self, children=None):
            super().__init__()
            self.children = children
            self.lookups = []
            self.destroy_calls = 0

        def findChildren(self, kind):
            self.lookups.append(kind)
            return list(self.children) if self.children is not None else super().findChildren(kind)

        def deleteLater(self):
            self.destroy_calls += 1
            super().deleteLater()

    class Window:
        def __init__(self, content):
            self._content = content

        def contentItem(self):
            return self._content

    class HostApplication(QObject):
        """CuraApplication's surface as far as this adapter reaches."""

        activityChanged = pyqtSignal()
        additionalComponentsChanged = pyqtSignal(str)

        def __init__(self, *, window=None, platform_activity=True):
            super().__init__()
            self.window = window
            self._platform = platform_activity
            self.components = {}          # QML file name -> host item, None, or the exception to raise
            self.requested = []
            self.joined = []
            self.removed = []
            self._additional_components = {}

        @property
        def platformActivity(self):
            return self._platform

        def set_platform_activity(self, value):
            self._platform = value

        def createQmlComponent(self, path):
            # The adapter hands Cura a native path (os.path.join of the
            # plugin directory and the host's file name), backslash-
            # spelled on Windows: key by the FILE NAME whichever
            # separator the platform produced, or the whole path becomes
            # the key and no host is ever found off POSIX.
            name = os.path.basename(path.replace("\\", "/"))
            self.requested.append(name)
            outcome = self.components.get(name)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def getMainWindow(self):
            return self.window

        def addAdditionalComponent(self, name, shell):
            self.joined.append((name, shell))
            self._additional_components.setdefault(name, []).append(shell)
            signal = getattr(self, "additionalComponentsChanged", None)
            if signal is not None:
                signal.emit(name)

        def removeAdditionalComponent(self, name, shell):
            self.removed.append((name, shell))
            rows = self._additional_components.get(name, [])
            rows[:] = [item for item in rows if item is not shell]

    class AddOnlyApplication(HostApplication):
        """Cura before removeAdditionalComponent: components are only added."""

        removeAdditionalComponent = None

    class SilentApplication(AddOnlyApplication):
        """And before additionalComponentsChanged existed."""

        additionalComponentsChanged = None

    class RefusingRemover(HostApplication):
        """Cura whose remover is there but blows up."""

        def removeAdditionalComponent(self, name, shell):
            self.removed.append((name, shell))
            raise RuntimeError("the component list is gone")

    class BlindPlatform(HostApplication):
        """Cura whose platform activity cannot be read."""

        @property
        def platformActivity(self):
            raise RuntimeError("no platform")

    class DeferredApplication(HostApplication):
        """Cura whose boot-complete edge drives the first host build."""

        initializationFinished = pyqtSignal()

    class CuraDouble(QObject):
        """CuraIntegration's surface here: the changed edge and the stage flag."""

        changed = pyqtSignal()

        def __init__(self, preview_active=False):
            super().__init__()
            self.preview_active = preview_active

        def set_preview(self, active):
            self.preview_active = active
            self.changed.emit()

else:
    # The host stdlib suite has no PyQt6: the class bodies below still
    # evaluate at import, so the Qt-bound doubles exist as None and the
    # Qt-marked tests skip before touching them.
    Card = None
    Host = None
    HostApplication = None
    AddOnlyApplication = None
    SilentApplication = None
    RefusingRemover = None
    BlindPlatform = None
    DeferredApplication = None
    CuraDouble = None

def mount(host, item, name=CARD_NAME):
        """The engine's own shape: the named card inside its host item."""
        item.setParentItem(host)
        item.setParent(host)
        item.setObjectName(name)
        return item


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class PreviewPresentationTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.module = self.qt.load("PreviewPresentation")
        self.Logger = self.module.Logger

    def presentation(self, application, *, cura=None, preview_active=False):
        cura = CuraDouble(preview_active) if cura is None else cura
        adapter = self.module.PreviewPresentation(application, cura)
        self.addCleanup(self._retire, adapter)
        return adapter

    def _retire(self, adapter):
        # Qt destroys any surviving shell at interpreter exit, long after the
        # adapter's own attributes are gone and its destroyed hook has nowhere
        # to land; retire the hosts while the object is still whole.
        adapter.close()
        self.qt.events(10)

    def _dispose(self, host):
        if not sip.isdeleted(host):
            sip.delete(host)

    def host(self, app, path, *, card=None, card_name=CARD_NAME, host=None):
        host = Host() if host is None else host
        host.setObjectName(path.rsplit(".", 1)[0])
        item = None
        if card is not NO_CARD and card_name is not None:
            item = mount(host, Card() if card is None else card, card_name)
        # Destroyed here, not by Qt's own shutdown, so the adapter's hook
        # still lands somewhere: see _retire.
        self.addCleanup(self._dispose, host)
        app.components[path] = host
        return host, item

    def build(self, *, window=True, panel=True, overlay=True, application=None,
              preview_active=False, platform_activity=True, panel_card=None, overlay_card=None,
              panel_host_cls=Host, overlay_host_cls=Host, card_name=CARD_NAME):
        if application is None:
            content = QQuickItem() if window else None
            application = HostApplication(window=Window(content) if window else None,
                                          platform_activity=platform_activity)
        else:
            win = application.getMainWindow()
            content = win.contentItem() if win is not None else None
        panel_host = panel_item = overlay_host = overlay_item = None
        if panel:
            panel_host, panel_item = self.host(application, PANEL_HOST, card=panel_card,
                                               card_name=card_name, host=panel_host_cls())
        if overlay:
            overlay_host, overlay_item = self.host(application, OVERLAY_HOST, card=overlay_card,
                                                   card_name=card_name, host=overlay_host_cls())
        cura = CuraDouble(preview_active)
        presentation = self.presentation(application, cura=cura)
        return SimpleNamespace(app=application, content=content, cura=cura, presentation=presentation,
                               panel_host=panel_host, panel_card=panel_item,
                               overlay_host=overlay_host, overlay_card=overlay_item)

    def test_the_pause_verdicts_push_to_every_card(self):
        # The single authority (the debt pack's two-clock
        # unification): the monitor model's verdicts land on BOTH
        # cards as the strip's six properties.
        scene = self.build()
        scene.presentation.publish_pause_verdicts(
            True, False, "busy", "not paused", "the lane is busy", "nothing to resume")
        for card in (scene.panel_card, scene.overlay_card):
            self.assertTrue(card.property("stripCanPause"))
            self.assertFalse(card.property("stripCanResume"))
            self.assertEqual(card.property("stripPauseReason"), "busy")
            self.assertEqual(card.property("stripResumeReason"), "not paused")
            self.assertEqual(card.property("stripPauseReasonDetail"), "the lane is busy")
            self.assertEqual(card.property("stripResumeReasonDetail"), "nothing to resume")

    def test_the_pause_verdicts_default_truthy_values_never_crash_the_push(self):
        # None and missing details coerce to the property defaults.
        scene = self.build()
        scene.presentation.publish_pause_verdicts(None, None, None, None)
        for card in (scene.panel_card, scene.overlay_card):
            self.assertFalse(card.property("stripCanPause"))
            self.assertFalse(card.property("stripCanResume"))
            self.assertEqual(card.property("stripPauseReason"), "")

    def test_the_panel_host_joins_cura_and_the_overlay_joins_the_window(self):
        scene = self.build()
        # The panel host is Cura's own action-panel row; the overlay host
        # is parented onto the window's content item, once.
        self.assertEqual(scene.app.requested, [PANEL_HOST, OVERLAY_HOST])
        self.assertEqual(scene.app.joined, [("saveButton", scene.panel_host)])
        self.assertIs(scene.overlay_host.parentItem(), scene.content)
        self.assertIs(scene.overlay_host.parent(), scene.content)
        self.assertEqual([host.lookups for host in (scene.panel_host, scene.overlay_host)],
                         [[QQuickItem], [QQuickItem]])
        self.assertEqual(scene.presentation.controls, (scene.panel_card, scene.overlay_card))

    def test_a_host_path_is_keyed_by_its_file_name_on_every_platform(self):
        # The adapter hands Cura a native path (os.path.join of the
        # plugin directory and the host's file name). On Windows that
        # path is backslash-spelled, and a "/"-only split kept the whole
        # path as the key: both hosts stayed unborn there and every
        # host-born assertion in this file came up empty (the CI logs).
        application = HostApplication(window=Window(QQuickItem()))
        application.components[PANEL_HOST] = "the panel host"
        self.assertEqual(application.createQmlComponent(r"D:\plugins" + "\\" + PANEL_HOST),
                         "the panel host")
        self.assertEqual(application.requested, [PANEL_HOST])

    def test_the_two_hostings_are_named_apart(self):
        scene = self.build()
        # The harness walks one tree and must tell the hostings apart.
        self.assertEqual(scene.panel_host.objectName(), "MoonrakerPreviewCardPanelHost")
        self.assertEqual(scene.overlay_host.objectName(), "MoonrakerPreviewCardOverlayHost")
        self.assertEqual(scene.panel_card.objectName(), CARD_NAME)
        self.assertEqual(scene.overlay_card.objectName(), "moonrakerPreviewCardOverlay")

    def test_the_hosts_are_built_once_and_their_creation_is_announced(self):
        app = HostApplication(window=Window(QQuickItem()))
        presentation = self.presentation(app)
        seen = []
        presentation.controlsChanged.connect(lambda: seen.append(1))
        self.assertEqual(seen, [])
        self.assertEqual(presentation.controls, ())

        _, panel_card = self.host(app, PANEL_HOST)
        _, overlay_card = self.host(app, OVERLAY_HOST)
        presentation.refresh()
        self.assertEqual(seen, [1])
        self.assertEqual(presentation.controls, (panel_card, overlay_card))

        presentation.refresh()
        self.assertEqual(seen, [1])  # nothing was created the second time
        self.assertEqual(app.requested.count(PANEL_HOST), 2)  # refused once, built once

    def test_without_a_main_window_only_the_panel_host_is_built(self):
        scene = self.build(window=False)
        # The overlay needs a content item to parent onto; Cura has no
        # window until it is up, and the panel host does not need one.
        self.assertEqual(scene.app.requested, [PANEL_HOST])
        self.assertEqual(scene.presentation.controls, (scene.panel_card,))
        scene.presentation.refresh()
        # Built once, and the overlay is never asked for without a window.
        self.assertEqual(scene.app.requested, [PANEL_HOST])

    def test_a_component_that_is_not_ready_is_retried_on_the_next_refresh(self):
        scene = self.build(panel=False, overlay=False)
        self.assertEqual(scene.presentation.controls, ())
        panel_host, panel_card = self.host(scene.app, PANEL_HOST)
        _, overlay_card = self.host(scene.app, OVERLAY_HOST)
        scene.cura.changed.emit()
        self.assertEqual(scene.presentation.controls, (panel_card, overlay_card))
        self.assertIs(scene.app.joined[0][1], panel_host)

    def test_a_failing_component_is_a_warning_and_the_next_refresh_recovers(self):
        app = HostApplication(window=Window(QQuickItem()))
        app.components[PANEL_HOST] = RuntimeError("no QML engine")
        presentation = self.presentation(app, preview_active=True)
        self.assertEqual(self.Logger.log.call_args[0][0], "w")
        self.assertIn("no QML engine", str(self.Logger.log.call_args[0]))
        self.assertEqual(presentation.controls, ())
        self.assertEqual(app.requested, [PANEL_HOST])  # the overlay is not attempted after the raise
        # The live stage still reached the presenter, so a later build gates right.
        self.assertTrue(presentation._values["previewStageActive"])

    def test_with_a_boot_edge_the_hosts_wait_for_it(self):
        # The card components are never created during plugin load:
        # Cura's QML modules are not registered until the boot
        # completes, and a card created in the storm is the session's
        # first QML import — the Windows Loading-UI stall's trigger.
        scene = self.build(application=DeferredApplication(window=Window(QQuickItem())))
        self.assertEqual(scene.app.requested, [])
        self.assertEqual(scene.app.joined, [])
        self.assertEqual(scene.presentation.controls, ())
        # The model's early fires cannot build shells in the storm either.
        scene.cura.changed.emit()
        self.assertEqual(scene.app.requested, [])
        seen = []
        scene.presentation.controlsChanged.connect(lambda: seen.append(1))
        # A value published pre-boot must flush onto the created cards.
        scene.presentation.publish({"configuredForFollowing": True})
        scene.app.initializationFinished.emit()
        self.assertEqual(scene.app.requested, [PANEL_HOST, OVERLAY_HOST])
        self.assertEqual(scene.app.joined, [("saveButton", scene.panel_host)])
        self.assertEqual(scene.presentation.controls, (scene.panel_card, scene.overlay_card))
        self.assertEqual(seen, [1])
        self.assertTrue(scene.panel_card.property("configuredForFollowing"))

    def test_a_late_construction_with_a_fired_boot_signal_boots_immediately(self):
        # The signal fired before the plugin was constructed: waiting
        # for an edge that will never come again keeps the Preview
        # hosts unborn (the late/hot activation parity).
        class StartedDeferredApplication(DeferredApplication):
            started = True

        scene = self.build(application=StartedDeferredApplication(window=Window(QQuickItem())))
        self.assertEqual(scene.app.requested, [PANEL_HOST, OVERLAY_HOST])
        self.assertEqual(scene.presentation.controls, (scene.panel_card, scene.overlay_card))

    def test_verdicts_published_before_the_boot_replay_onto_the_created_cards(self):
        # The monitor's first action edge can land while the cards are
        # still boot-deferred; the verdicts must not fall on the floor.
        scene = self.build(application=DeferredApplication(window=Window(QQuickItem())))
        scene.presentation.publish_pause_verdicts(
            True, False, "busy", "not paused", "the lane is busy", "nothing to resume")
        scene.app.initializationFinished.emit()
        for card in (scene.panel_card, scene.overlay_card):
            self.assertTrue(card.property("stripCanPause"))
            self.assertFalse(card.property("stripCanResume"))
            self.assertEqual(card.property("stripPauseReason"), "busy")
            self.assertEqual(card.property("stripResumeReasonDetail"), "nothing to resume")

    def test_a_presentation_closed_before_the_boot_stays_closed(self):
        app = DeferredApplication(window=Window(QQuickItem()))
        presentation = self.presentation(app)
        presentation.close()
        app.initializationFinished.emit()
        self.assertEqual(app.requested, [])
        self.assertEqual(presentation.controls, ())
        # Even a manual refresh must not resurrect a closed adapter.
        self.host(app, PANEL_HOST)
        self.host(app, OVERLAY_HOST)
        presentation.refresh()
        self.assertEqual(app.requested, [])
        self.assertEqual(presentation.controls, ())

    def test_the_corner_overlay_opens_only_while_cura_hides_its_panel(self):
        scene = self.build()
        scene.presentation.publish({"configuredForFollowing": True, "previewStageActive": True})
        self.assertTrue(scene.panel_card.property("gateVisible"))
        self.assertFalse(scene.overlay_card.property("gateVisible"))

        # The idle platform: Cura hides its own action panel, and the panel
        # host with it — the corner overlay is the one that takes over.
        scene.app.set_platform_activity(False)
        scene.app.activityChanged.emit()
        self.assertTrue(scene.panel_card.property("gateVisible"))  # its gate is the stage; the hiding is Cura's
        self.assertTrue(scene.overlay_card.property("gateVisible"))

        scene.app.set_platform_activity(True)
        scene.app.activityChanged.emit()
        self.assertFalse(scene.overlay_card.property("gateVisible"))

    def test_no_card_shows_without_configured_following_and_a_live_stage(self):
        for configured, stage in ((False, True), (True, False), (False, False)):
            with self.subTest(configured=configured, stage=stage):
                scene = self.build(preview_active=stage)
                scene.presentation.publish({"configuredForFollowing": configured})
                self.assertFalse(scene.panel_card.property("gateVisible"))
                self.assertFalse(scene.overlay_card.property("gateVisible"))

    def test_the_platform_edge_republishes_the_gate_without_a_rebuild(self):
        scene = self.build(preview_active=True)
        scene.presentation.publish({"configuredForFollowing": True})
        built = list(scene.app.requested)
        scene.app.set_platform_activity(False)
        scene.app.activityChanged.emit()
        # The gate moved on the platform's own edge; no component was touched.
        self.assertTrue(scene.overlay_card.property("gateVisible"))
        self.assertEqual(scene.app.requested, built)

    def test_presenter_values_reach_the_cards_but_never_override_the_gate(self):
        scene = self.build()
        scene.presentation.publish({"loadBusy": True, "followingPaused": False, "gateVisible": True})
        for card in (scene.panel_card, scene.overlay_card):
            self.assertTrue(card.property("loadBusy"))
            self.assertFalse(card.property("followingPaused"))
            # A caller's gateVisible is not a value: the gate is this module's call.
            self.assertFalse(card.property("gateVisible"))

    def test_a_deleted_card_never_takes_the_other_host_down(self):
        scene = self.build(panel_card=DeletedCard())
        scene.presentation.publish({"configuredForFollowing": True, "previewStageActive": True})
        self.assertFalse(scene.overlay_card.property("gateVisible"))  # the panel is up
        scene.app.set_platform_activity(False)
        scene.app.activityChanged.emit()
        self.assertTrue(scene.overlay_card.property("gateVisible"))

    def test_an_unreadable_platform_predicate_errs_towards_the_overlay(self):
        app = BlindPlatform(window=Window(QQuickItem()))
        self.host(app, PANEL_HOST)
        self.host(app, OVERLAY_HOST)
        presentation = self.presentation(app, preview_active=True)
        presentation.publish({"configuredForFollowing": True})
        self.assertTrue(presentation.controls[1].property("gateVisible"))

    def test_the_card_signals_become_plugin_intents(self):
        scene = self.build()
        intents = (
            ("loadClicked", (), "loadRequested"),
            ("pauseClicked", (), "attachmentRequested"),
            ("improveEtaRequested", (), "improveEtaRequested"),
            ("pauseAtLayerRequested", (7,), "pauseAtLayerRequested"),
            ("printPauseRequested", (), "printPauseRequested"),
            ("removePauseAtLayerRequested", (9,), "removePauseRequested"),
            ("clearPauseAtLayersRequested", (), "clearPausesRequested"),
            ("bedMeshVisibilityRequested", (False,), "bedMeshVisibilityRequested"),
            ("bedMeshThresholdsRequested", (-1.0, 1.0), "bedMeshThresholdsRequested"),
            ("bedMeshExaggerationRequested", (2.5,), "bedMeshExaggerationRequested"),
        )
        for card_signal, payload, intent in intents:
            with self.subTest(signal=card_signal):
                seen = []
                # The sink is bound, not closed over: each intent needs its own.
                getattr(scene.presentation, intent).connect(lambda *args, sink=seen: sink.append(args))
                getattr(scene.panel_card, card_signal).emit(*payload)
                self.assertEqual(seen, [payload])

    def test_a_card_without_the_newer_signals_wires_the_rest(self):
        # The card and the adapter version apart: an absent name is skipped,
        # the names both carry stay wired.
        scene = self.build(panel_card=OldCard())
        seen = []
        scene.presentation.loadRequested.connect(lambda: seen.append(1))
        scene.panel_card.loadClicked.emit()
        self.assertEqual(seen, [1])
        self.assertEqual(len(scene.presentation.controls), 2)  # still adopted

    def test_a_host_carrying_a_foreign_card_is_not_wired(self):
        scene = self.build(card_name="someOtherCard")
        self.assertEqual(scene.presentation.controls, ())
        # The host still joins Cura's row; only the card wiring is skipped.
        self.assertEqual(scene.app.joined, [("saveButton", scene.panel_host)])

    def test_an_unreadable_item_does_not_blind_the_card_search(self):
        app = HostApplication(window=Window(QQuickItem()))
        host = Host()
        card = mount(host, Card())
        host.children = [BlindItem(), card]
        app.components[PANEL_HOST] = host
        presentation = self.presentation(app)
        self.assertEqual(presentation.controls, (card,))
        seen = []
        presentation.loadRequested.connect(lambda: seen.append(1))
        card.loadClicked.emit()
        self.assertEqual(seen, [1])

    def test_a_destroyed_host_is_rebuilt_on_the_next_refresh(self):
        scene = self.build()
        scene.presentation.publish({"configuredForFollowing": True})
        old_host = scene.panel_host
        old_host.deleteLater()
        self.qt.events(10)  # the deferred delete fires the destroyed edge
        self.assertEqual(scene.presentation.controls, (scene.overlay_card,))
        self.assertIs(scene.app.removed[0][1], old_host)

        new_host, new_card = self.host(scene.app, PANEL_HOST)
        scene.cura.changed.emit()
        self.assertEqual(scene.presentation.controls, (new_card, scene.overlay_card))
        self.assertEqual(scene.app.joined[-1], ("saveButton", new_host))

    def test_a_destroyed_overlay_leaves_the_panel_alone(self):
        scene = self.build()
        scene.overlay_host.deleteLater()
        self.qt.events(10)
        self.assertEqual(scene.presentation.controls, (scene.panel_card,))

    def test_close_retires_both_hosts_and_survives_a_repeat(self):
        scene = self.build()
        scene.presentation.publish({"configuredForFollowing": True, "previewStageActive": True})
        built = list(scene.app.requested)
        scene.presentation.close()

        self.assertIs(scene.app.removed[0][1], scene.panel_host)
        self.assertFalse(scene.panel_card.property("visible"))
        self.assertEqual(scene.panel_card.destroy_calls, 1)
        self.assertEqual(scene.overlay_card.destroy_calls, 1)
        self.assertEqual(scene.panel_host.destroy_calls, 1)
        self.assertEqual(scene.overlay_host.destroy_calls, 1)
        self.assertEqual(scene.presentation.controls, ())

        # A closed presentation is inert, and closing twice is a no-op.
        scene.presentation.refresh()
        scene.cura.changed.emit()
        self.assertEqual(scene.app.requested, built)
        scene.presentation.close()
        self.assertEqual(scene.presentation.controls, ())

    def test_close_survives_hosts_that_are_already_gone(self):
        scene = self.build(panel_card=DeletedCard(), panel_host_cls=DeletedHost)
        scene.presentation.close()
        self.assertEqual(scene.overlay_card.destroy_calls, 1)
        self.assertEqual(scene.overlay_host.destroy_calls, 1)
        self.assertEqual(scene.presentation.controls, ())

    def test_the_add_only_component_api_still_retires_the_panel_host(self):
        app = SilentApplication(window=Window(QQuickItem()))
        scene = self.build(application=app)
        self.assertEqual(app._additional_components["saveButton"], [scene.panel_host])
        scene.presentation.close()
        self.assertEqual(app._additional_components["saveButton"], [])

    def test_the_announced_removal_survives_the_add_only_api(self):
        app = AddOnlyApplication(window=Window(QQuickItem()))
        announced = []
        app.additionalComponentsChanged.connect(announced.append)
        scene = self.build(application=app)
        announced.clear()  # the add announced the row too
        scene.presentation.close()
        self.assertEqual(app._additional_components.get("saveButton"), [])
        self.assertEqual(announced, ["saveButton"])

    def test_a_remover_that_raises_falls_back_to_the_component_list(self):
        app = RefusingRemover(window=Window(QQuickItem()))
        scene = self.build(application=app)
        scene.presentation.close()
        self.assertEqual(app._additional_components["saveButton"], [])
        self.assertEqual(app.removed, [("saveButton", scene.panel_host)])

    def test_a_component_list_of_the_wrong_shape_is_left_alone(self):
        app = AddOnlyApplication(window=Window(QQuickItem()))
        scene = self.build(application=app)
        app._additional_components["saveButton"] = "not a list"
        scene.presentation.close()
        self.assertEqual(app._additional_components["saveButton"], "not a list")

    def test_refresh_takes_the_live_stage_over_a_stale_pushed_value(self):
        scene = self.build(preview_active=False)
        scene.presentation.publish({"configuredForFollowing": True, "previewStageActive": True})
        self.assertTrue(scene.panel_card.property("gateVisible"))
        scene.cura.set_preview(False)  # the stage was left; refresh publishes the live reading
        self.assertFalse(scene.panel_card.property("gateVisible"))


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
