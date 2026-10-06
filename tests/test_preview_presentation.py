"""Behavioural tests for the Cura/QML presentation adapter.

PreviewPresentation is the seam between Cura's QML card and the plugin's
user intents: it hosts one card inside Cura's action panel and one in the
corner overlay, decides which of the two is on, pushes the presenter's
values onto the live card, and turns the card's own signals into plugin
intents. The doubles here are real QQuickItems — parenting, child lookup,
the destroyed signal and deleteLater are genuine Qt — and the card is a
real item carrying the QML signal surface, so the gates, the value push
and the wiring are asserted as behaviour rather than as source text.

The adapter's host lifecycle and banner projection paths run under this suite.
The approximation is on the engine side: Cura builds the hosts
(createQmlComponent) from its own QML modules, which this container does
not carry, so the items handed back here are built in-process and mirror
the two host documents. Loading the real QML files on the engine is the
release harness's job (tools/check_qml_engine.py, tools/capture_preview.py).
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from qt_runtime_support import QT_AVAILABLE, runtime

PANEL_HOST = "MoonrakerPreviewCardPanelHost.qml"
OVERLAY_HOST = "MoonrakerPreviewCardOverlayHost.qml"
TAGS_HOST = "PreviewObjectTagsHost.qml"
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
        cardExpandedRequested = pyqtSignal(bool)
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

    class TagsHost(Host):
        lightingEnabledRequested = pyqtSignal(bool)
        lightBedRequested = pyqtSignal(bool)
        lightModelsRequested = pyqtSignal(bool)
        bedMeshVisibilityRequested = pyqtSignal(bool)
        bedMeshThresholdsRequested = pyqtSignal(float, float)
        bedMeshExaggerationRequested = pyqtSignal(float)
        tagsEnabledRequested = pyqtSignal(bool)
        toolheadVisibilityRequested = pyqtSignal(bool)
        toolheadSetupRequested = pyqtSignal()
        reportedPositionRequested = pyqtSignal(bool)
        toolheadOpacityRequested = pyqtSignal(float)
        hoverOnlyRequested = pyqtSignal(bool)
        pointerMoved = pyqtSignal(float, float)

    class Window:
        def __init__(self, content):
            self._content = content

        def contentItem(self):
            return self._content

    class RenderWindow(Window):
        viewportRect = SimpleNamespace(x=lambda: 0, y=lambda: 0,
                                       width=lambda: 1, height=lambda: 1)

        def width(self): return 800
        def height(self): return 600
        def devicePixelRatio(self): return 1

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
        has_toolpath = False
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
    TagsHost = None
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

    def presentation(self, application, *, cura=None, preview_active=False, persistence=None):
        cura = CuraDouble(preview_active) if cura is None else cura
        adapter = self.module.PreviewPresentation(application, cura, persistence=persistence)
        self.addCleanup(self._retire, adapter)
        return adapter

    def test_toolhead_visibility_persists_and_restores_without_losing_other_view_choices(self):
        document = {"previewReportedToolhead": True, "previewToolheadOpacity": .6}
        persistence = SimpleNamespace(state_global_document=lambda: dict(document), merge_state_global=document.update)
        adapter = self.presentation(HostApplication(), persistence=persistence)
        self.assertTrue(adapter.toolhead_visible)
        signals = []
        adapter.toolheadVisibilityRequested.connect(signals.append)
        adapter._set_toolhead_visible(False)
        self.assertEqual(signals, [False])
        self.assertFalse(document["previewToolheadVisible"])
        restored = self.presentation(HostApplication(), persistence=persistence)
        self.assertFalse(restored.toolhead_visible)
        self.assertTrue(restored.reported_position)
        self.assertAlmostEqual(restored.toolhead_opacity, .6)
        restored._set_toolhead_visible(True)
        self.assertTrue(document["previewToolheadVisible"])

    def test_custom_model_availability_reaches_view_options_on_configuration_change(self):
        app = HostApplication(window=Window(QQuickItem()))
        tags = TagsHost()
        app.components["PreviewObjectTagsHost.qml"] = tags
        adapter = self.presentation(app)
        self.assertFalse(tags.property("customToolheadAvailable"))
        adapter.publish({"customToolheadAvailable": True})
        self.assertTrue(tags.property("customToolheadAvailable"))
        adapter.publish({"customToolheadAvailable": False})
        self.assertFalse(tags.property("customToolheadAvailable"))

    def test_setup_opens_current_printer_machine_action_and_reuses_dialog(self):
        app = HostApplication(window=Window(QQuickItem()))
        action, dialog = Mock(), Mock()
        app.getMachineActionManager = lambda: SimpleNamespace(getMachineAction=lambda key: action)
        adapter = self.presentation(app)
        app.createQmlComponent = Mock(return_value=dialog)
        with patch.object(self.module.QMetaObject, "invokeMethod") as invoke:
            adapter._open_toolhead_settings()
            adapter._open_toolhead_settings()
        self.assertEqual(invoke.call_count, 2)
        invoke.assert_called_with(dialog, "openToolheadSettings")
        app.createQmlComponent.assert_called_once()
        self.assertEqual(app.createQmlComponent.call_args.args[1], {"configurationManager": action})

    def test_lighting_master_persists_without_erasing_receiver_choices(self):
        document = {"previewLightBed": False, "previewLightModels": True}
        persistence = SimpleNamespace(state_global_document=lambda: dict(document), merge_state_global=document.update)
        app = HostApplication(window=Window(QQuickItem()))
        tags = TagsHost()
        app.components["PreviewObjectTagsHost.qml"] = tags
        adapter = self.presentation(app, persistence=persistence)
        changes = []
        adapter.sceneLightingRequested.connect(lambda: changes.append(adapter.lighting_enabled))
        self.assertTrue(adapter.lighting_enabled)
        self.assertTrue(tags.property("lightingEnabled"))
        tags.lightingEnabledRequested.emit(False)
        self.assertFalse(tags.property("lightingEnabled"))
        self.assertFalse(adapter.light_bed)
        self.assertTrue(adapter.light_models)
        restored = self.presentation(HostApplication(), persistence=persistence)
        self.assertFalse(restored.lighting_enabled)
        tags.lightingEnabledRequested.emit(True)
        self.assertEqual(changes, [False, True])
        self.assertEqual(document, {"previewLightingEnabled": True, "previewLightBed": False, "previewLightModels": True})

    def test_scene_lighting_choices_persist_and_bed_mesh_requests_route_from_view_options(self):
        document = {}
        persistence = SimpleNamespace(state_global_document=lambda: dict(document), merge_state_global=document.update)
        app = HostApplication(window=Window(QQuickItem()))
        tags = TagsHost()
        app.components["PreviewObjectTagsHost.qml"] = tags
        adapter = self.presentation(app, persistence=persistence)
        changes = []
        adapter.sceneLightingRequested.connect(lambda: changes.append(True))
        tags.lightBedRequested.emit(False)
        tags.lightModelsRequested.emit(False)
        self.assertEqual(len(changes), 2)
        self.assertEqual(document, {"previewLightBed": False, "previewLightModels": False})
        restored = self.presentation(HostApplication(), persistence=persistence)
        self.assertFalse(restored.light_bed)
        self.assertFalse(restored.light_models)
        adapter.publish({"bedMeshAvailable": True, "bedMeshExaggeration": 50.0})
        self.assertTrue(tags.property("bedMeshAvailable"))
        self.assertEqual(tags.property("bedMeshExaggeration"), 50.0)
        for signal, args in (("bedMeshVisibilityRequested", (False,)),
                             ("bedMeshThresholdsRequested", (-.1, .2)),
                             ("bedMeshExaggerationRequested", (35.0,))):
            calls = []
            getattr(adapter, signal).connect(lambda *values, calls=calls: calls.append(values))
            getattr(tags, signal).emit(*args)
            self.assertEqual(calls, [args])

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

    def test_hover_reads_curas_depth_tested_selection_pass(self):
        state = self.build(preview_active=True)
        selected = object()
        positions = []
        render_pass = SimpleNamespace(getIdAtPosition=lambda x, y: positions.append((x, y)) or 27)
        state.app.getRenderer = lambda: SimpleNamespace(getRenderPass=lambda name: render_pass if name == "selection" else None)
        scene = SimpleNamespace(findObject=lambda object_id: selected if object_id == 27 else None)
        window = SimpleNamespace(width=lambda: 800, height=lambda: 600)
        state.presentation._pick_available = True
        state.presentation._pointer_moved(400, 300)
        self.assertEqual(state.presentation._pick_hover(window, scene), id(selected))
        self.assertEqual(positions, [(0.0, 0.0)])
        state.presentation._pointer_moved(-1, -1)
        self.assertEqual(state.presentation._pick_hover(window, scene), 0)

    def test_gcode_hover_uses_a_camera_ray_and_keeps_all_shared_footprints(self):
        state = self.build(preview_active=True)
        positions = []
        ray = SimpleNamespace(origin=SimpleNamespace(x=0, y=10, z=0),
                              direction=SimpleNamespace(x=0, y=-1, z=0))
        camera = SimpleNamespace(getRay=lambda x, y: positions.append((x, y)) or ray)
        window = SimpleNamespace(width=lambda: 800, height=lambda: 600)
        objects = [{"name": name, "footprint": [(-2, -2), (2, -2), (2, 2), (-2, 2)],
                    "footprintHeight": 0} for name in ("one", "two")]
        state.presentation._pointer_moved(400, 300)
        self.assertEqual(state.presentation._pick_footprints(window, camera, objects), ["one", "two"])
        self.assertEqual(positions, [(0.0, 0.0)])
        state.presentation._pointer_moved(-1, -1)
        self.assertEqual(state.presentation._pick_footprints(window, camera, objects), [])

    def test_the_live_tags_host_projects_matching_gcode_and_all_shared_hover_hits(self):
        import sys
        from types import ModuleType
        from unittest.mock import Mock, patch

        content = QQuickItem()
        app = HostApplication(window=RenderWindow(content))
        shell = TagsHost()
        app.components[TAGS_HOST] = shell
        scene = self.build(application=app, preview_active=True)
        camera = SimpleNamespace(
            projectToViewport=lambda position: (position.x, position.z),
            getViewportWidth=lambda: 800, getViewportHeight=lambda: 600,
            getRay=lambda x, y: SimpleNamespace(
                origin=SimpleNamespace(x=0, y=10, z=0),
                direction=SimpleNamespace(x=0, y=-1, z=0)),
        )
        cura_scene = SimpleNamespace(
            getRoot=lambda: SimpleNamespace(getAllChildren=lambda: []),
            getActiveCamera=lambda: camera,
        )
        scene.cura.controller = SimpleNamespace(getScene=lambda: cura_scene)
        scene.cura.has_toolpath = True
        scene.cura.selected_layer = 0
        scene.cura.heights = [2.0]
        vector_module = ModuleType("UM.Math.Vector")
        vector_module.Vector = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
        vector_patch = patch.dict(sys.modules, {"UM.Math": ModuleType("UM.Math"),
                                                "UM.Math.Vector": vector_module})
        vector_patch.start()
        self.addCleanup(vector_patch.stop)
        saved = Mock()
        scene.presentation._persistence = SimpleNamespace(merge_state_global=saved)
        app.getGlobalContainerStack = lambda: SimpleNamespace(getProperty=lambda key, scope: {
            "machine_width": 220, "machine_depth": 220, "machine_center_is_zero": True}[key])
        app.getRenderer = lambda: SimpleNamespace(getRenderPass=lambda name: None)
        definitions = [{"name": name, "center": [0, 0], "bounds": [-2, -2, 2, 2]}
                       for name in ("one", "two")]
        metrics = {name: {"progress": 0.5, "deadline": None, "top": 4.0}
                   for name in ("one", "two")}
        scene.presentation.publish({"configuredForFollowing": True,
                                    "previewStageActive": True,
                                    "objectTagSourceActive": True,
                                    "objectTagDefinitions": definitions,
                                    "objectTagMetrics": metrics})
        self.assertTrue(shell.property("dockVisible"))
        shell.tagsEnabledRequested.emit(True)
        saved.assert_called_with({"previewObjectTagsEnabled": True})
        self.assertEqual(shell.property("pickMode"), "footprint")
        self.assertEqual(len(shell.property("tagRows")), 2)
        self.assertEqual(shell.property("tagRows")[0]["progress"], 0.5)
        shell.pointerMoved.emit(400, 300)
        shell.hoverOnlyRequested.emit(True)
        saved.assert_called_with({"previewObjectTagsHoverOnly": True})
        self.assertEqual(shell.property("hoveredNames"), ["one", "two"])
        self.assertEqual(len(shell.property("tagRows")), 2)
        with patch.object(scene.presentation._viewport_hover, "blocked", return_value=True):
            scene.presentation._update_tags()
            self.assertEqual(shell.property("hoveredNames"), [])
            self.assertEqual(shell.property("tagRows"), [])
            self.assertIsNone(scene.presentation._footprint_picked_point)
        scene.presentation._update_tags()
        self.assertEqual(shell.property("hoveredNames"), ["one", "two"])
        scene.presentation._update_tags()  # same ray and rows reuse the projection
        scene.panel_card.cardExpandedRequested.emit(False)
        saved.assert_called_with({"previewCardExpanded": False})
        scene.presentation.publish({"objectTagSourceActive": False})
        self.assertEqual(shell.property("tagRows"), [])
        scene.presentation.publish({"previewStageActive": False})
        self.assertFalse(shell.property("dockVisible"))

    def test_idle_banner_objects_reuse_data_until_scene_or_authoritative_inputs_change(self):
        class Scene(QObject):
            sceneChanged = pyqtSignal(object)

        state = self.build(preview_active=True)
        host_scene = Scene()
        root = object()
        host_scene.getRoot = lambda: root
        dimensions = {"machine_width": 250, "machine_depth": 250, "machine_center_is_zero": False}
        stack = SimpleNamespace(getProperty=lambda name, _role: dimensions[name])
        state.presentation._application.getGlobalContainerStack = lambda: stack
        state.cura.controller = SimpleNamespace(getScene=lambda: host_scene)
        state.cura.selected_layer, state.cura.heights = 0, [0.2, 0.4]
        presentation = state.presentation
        rows = ([{"name": "part"}], False)
        with patch.object(presentation, "_build_scene_objects", return_value=rows) as build:
            for _ in range(30):
                self.assertIs(presentation._scene_objects(), rows)
            build.assert_called_once()
            presentation.publish({"objectTagMetrics": {"part": {"progress": 0.2}}})
            presentation._scene_objects()
            self.assertEqual(build.call_count, 2)
            presentation.publish({"objectTagMetrics": {"part": {"progress": 0.2}}})
            presentation._scene_objects()
            self.assertEqual(build.call_count, 2)
            host_scene.sceneChanged.emit(root)
            presentation._scene_objects()
            self.assertEqual(build.call_count, 3)
            state.cura.selected_layer = 1
            presentation._scene_objects()
            self.assertEqual(build.call_count, 4)
            dimensions["machine_width"] = 300
            presentation._scene_objects()
            self.assertEqual(build.call_count, 5)
            presentation.close()
            self.assertEqual(host_scene.receivers(host_scene.sceneChanged), 0)

    def test_gcode_fallback_uses_polygon_centres_bounds_and_selected_layer(self):
        import sys
        from types import ModuleType
        from unittest.mock import patch

        state = self.build(preview_active=True)
        scene = SimpleNamespace(getRoot=lambda: SimpleNamespace(getAllChildren=lambda: []))
        state.cura.controller = SimpleNamespace(getScene=lambda: scene)
        state.cura.selected_layer = 0
        state.cura.heights = [3.0]
        state.app.getGlobalContainerStack = lambda: SimpleNamespace(
            getProperty=lambda key, scope: {"machine_width": 200, "machine_depth": 200,
                                            "machine_center_is_zero": False}[key])
        state.presentation._values = {
            "objectTagDefinitions": [
                {"name": "polygon", "polygon": [(95, 95), (105, 95), (100, 105)]},
                {"name": "bounds", "center": [50, 50], "bounds": [48, 48, 52, 52],
                 "excluded": True},
                {"name": "unplaced"},
            ],
            "objectTagMetrics": {"polygon": {"progress": 0.25, "top": 2.0},
                                 "bounds": {"progress": 0.8}},
            "objectTagHeight": 1.0,
        }
        vector_module = ModuleType("UM.Math.Vector")
        vector_module.Vector = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
        with patch.dict(sys.modules, {"UM.Math": ModuleType("UM.Math"),
                                      "UM.Math.Vector": vector_module}):
            rows, pickable = state.presentation._scene_objects()
        self.assertFalse(pickable)
        self.assertEqual([row["name"] for row in rows], ["polygon", "bounds"])
        self.assertEqual((rows[0]["position"].x, rows[0]["position"].y), (0, 2))
        self.assertEqual(rows[0]["progress"], 0.25)
        self.assertEqual(rows[1]["footprint"], [(-52, 52), (-48, 52), (-48, 48), (-52, 48)])
        self.assertIsNone(rows[1]["progress"])

    def test_scene_mesh_filtering_and_exclusion_keep_only_visible_objects(self):
        import sys
        from types import ModuleType
        from unittest.mock import patch

        state = self.build(preview_active=True)
        def node(name, *, selected=True, valid=True, broken=False, x=0):
            if broken:
                return SimpleNamespace(isSelectable=lambda: (_ for _ in ()).throw(RuntimeError("gone")))
            box = SimpleNamespace(center=SimpleNamespace(x=x, z=0), top=10, bottom=0,
                                  height=10, isValid=lambda: valid)
            return SimpleNamespace(isSelectable=lambda: selected, getMeshData=lambda: object(),
                                   isVisible=lambda: True, getBoundingBox=lambda: box,
                                   getName=lambda: name)
        nodes = [node("hidden", selected=False), node("invalid", valid=False),
                 node("", x=50), node("mesh", x=50), node("Part"), node("", broken=True)]
        scene = SimpleNamespace(getRoot=lambda: SimpleNamespace(getAllChildren=lambda: nodes))
        state.cura.controller = SimpleNamespace(getScene=lambda: scene)
        state.app.getGlobalContainerStack = lambda: SimpleNamespace(
            getProperty=lambda key, scope: {"machine_width": 220, "machine_depth": 220,
                                            "machine_center_is_zero": True}[key])
        state.presentation._values = {
            "objectTagDefinitions": [{"name": "Part", "center": [0, 0], "excluded": True}],
            "objectTagMetrics": {"Part": {"progress": 0.7}},
        }
        vector_module = ModuleType("UM.Math.Vector")
        vector_module.Vector = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
        with patch.dict(sys.modules, {"UM.Math": ModuleType("UM.Math"),
                                      "UM.Math.Vector": vector_module}):
            rows, pickable = state.presentation._scene_objects()
        self.assertTrue(pickable)
        self.assertEqual([row["name"] for row in rows], ["mesh", "Part"])
        self.assertIsNone(rows[1]["progress"])

    def test_hover_cache_and_unavailable_renderers_preserve_safe_results(self):
        from unittest.mock import Mock

        state = self.build(preview_active=True)
        selected = object()
        render_pass = SimpleNamespace(getIdAtPosition=Mock(return_value=7))
        state.app.getRenderer = lambda: SimpleNamespace(getRenderPass=lambda name: render_pass)
        scene = SimpleNamespace(findObject=lambda node_id: selected)
        window = SimpleNamespace(width=lambda: 800, height=lambda: 600)
        state.presentation._pick_available = True
        state.presentation._pointer_moved(400, 300)
        self.assertEqual(state.presentation._pick_hover(window, scene), id(selected))
        self.assertEqual(state.presentation._pick_hover(window, scene), id(selected))
        render_pass.getIdAtPosition.assert_called_once()
        state.presentation._pointer_moved(401, 300)
        state.app.getRenderer = lambda: SimpleNamespace(getRenderPass=lambda name: None)
        self.assertEqual(state.presentation._pick_hover(window, scene), 0)
        state.app.getRenderer = lambda: (_ for _ in ()).throw(RuntimeError("renderer gone"))
        self.assertEqual(state.presentation._pick_hover(window, scene), 0)
        state.presentation._pointer_moved(400, 300)
        bad_camera = SimpleNamespace(getRay=lambda x, y: (_ for _ in ()).throw(RuntimeError("ray gone")))
        self.assertEqual(state.presentation._pick_footprints(window, bad_camera, []), [])

    def test_banners_clear_when_the_loaded_file_is_not_the_active_print(self):
        from unittest.mock import Mock

        state = self.build(preview_active=True)
        camera = SimpleNamespace(projectToViewport=lambda position: position,
                                 getViewportWidth=lambda: 800, getViewportHeight=lambda: 600)
        state.cura.controller = SimpleNamespace(
            getScene=lambda: SimpleNamespace(getActiveCamera=lambda: camera))
        state.cura.has_toolpath = True
        shell = Host()
        state.presentation._tags_shell = shell
        state.presentation._tags_enabled = True
        state.presentation._projected_rows = [{"name": "stale"}]
        state.presentation._scene_objects = Mock(side_effect=AssertionError("stale objects must not project"))
        state.presentation._values["objectTagSourceActive"] = False
        state.presentation._update_tags()
        self.assertEqual(shell.property("tagRows"), [])
        state.presentation._scene_objects.assert_not_called()

    def test_current_object_height_fallback_keeps_the_status_name_after_case_join(self):
        import sys
        from types import ModuleType
        from unittest.mock import patch

        state = self.build(preview_active=True)
        box = SimpleNamespace(center=SimpleNamespace(x=0, z=0), top=10, bottom=0,
                              height=10, isValid=lambda: True)
        node = SimpleNamespace(isSelectable=lambda: True, getMeshData=lambda: object(),
                               isVisible=lambda: True, getBoundingBox=lambda: box,
                               getName=lambda: "mesh")
        scene = SimpleNamespace(getRoot=lambda: SimpleNamespace(getAllChildren=lambda: [node]))
        state.cura.controller = SimpleNamespace(getScene=lambda: scene)
        state.app.getGlobalContainerStack = lambda: SimpleNamespace(
            getProperty=lambda key, scope: {"machine_width": 220, "machine_depth": 220,
                                            "machine_center_is_zero": True}[key])
        state.presentation._values = {
            "objectTagDefinitions": [{"name": "Mixed", "statusName": "UPPER", "center": [0, 0]}],
            "objectTagMetrics": {"Mixed": {"progress": None}},
            "objectTagHeight": 5, "objectTagCurrentObject": "UPPER",
        }
        vector_module = ModuleType("UM.Math.Vector")
        vector_module.Vector = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
        with patch.dict(sys.modules, {"UM.Math": ModuleType("UM.Math"),
                                      "UM.Math.Vector": vector_module}):
            rows, pickable = state.presentation._scene_objects()
        self.assertTrue(pickable)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Mixed")
        self.assertEqual(rows[0]["progress"], 0.5)

    def test_collapsing_either_card_keeps_both_hosts_in_sync(self):
        scene = self.build(preview_active=True)
        self.assertTrue(scene.panel_card.property("cardExpanded"))
        self.assertTrue(scene.overlay_card.property("cardExpanded"))
        scene.panel_card.cardExpandedRequested.emit(False)
        self.assertFalse(scene.panel_card.property("cardExpanded"))
        self.assertFalse(scene.overlay_card.property("cardExpanded"))
        scene.overlay_card.cardExpandedRequested.emit(True)
        self.assertTrue(scene.panel_card.property("cardExpanded"))
        self.assertTrue(scene.overlay_card.property("cardExpanded"))

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
        self.assertEqual(scene.app.requested, [PANEL_HOST, OVERLAY_HOST, TAGS_HOST])
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
        self.assertEqual(scene.app.requested, [PANEL_HOST, OVERLAY_HOST, TAGS_HOST])
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
        self.assertEqual(scene.app.requested, [PANEL_HOST, OVERLAY_HOST, TAGS_HOST])
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

    def test_only_the_current_card_is_told_to_raise_the_prompt(self):
        # The prompt is a Popup, and a Popup renders in the WINDOW's
        # overlay: it escapes whatever hidden ancestor holds its card,
        # so publishing the value to both hostings put two identical
        # dialogs on screen at once (measured, in the local gate run's
        # own screenshots). The card the model considers current is the
        # one that may ask — the overlay while Cura hides its panel,
        # the panel while it shows it.
        scene = self.build()
        scene.presentation.publish({"configuredForFollowing": True,
                                    "previewStageActive": True,
                                    "replacePromptVisible": True})
        self.assertTrue(scene.panel_card.property("replacePromptVisible"))
        self.assertFalse(scene.overlay_card.property("replacePromptVisible"))

        scene.app.set_platform_activity(False)
        scene.app.activityChanged.emit()
        self.assertFalse(scene.panel_card.property("replacePromptVisible"))
        self.assertTrue(scene.overlay_card.property("replacePromptVisible"))

    def test_a_withdrawn_prompt_stays_withdrawn_on_the_hidden_card(self):
        # The other direction: the card that is NOT current must read
        # False, never a stale True from the turn it was current.
        scene = self.build()
        scene.presentation.publish({"configuredForFollowing": True,
                                    "previewStageActive": True,
                                    "replacePromptVisible": True})
        scene.app.set_platform_activity(False)
        scene.app.activityChanged.emit()
        scene.presentation.publish({"replacePromptVisible": False})
        for card in (scene.panel_card, scene.overlay_card):
            self.assertFalse(card.property("replacePromptVisible"))

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
