"""Coverage for the bed-mesh scene node and the presenter that owns it.

The node runs against recording UM doubles (SceneNode, MeshData, OpenGL),
so every geometry decision — the bed-edge extension, Klipper's clamp, the
neon ribbon, the colours — is asserted on the buffers handed to Cura. The
presenter runs through the harness's real Qt wiring and a faithful
presentation/persistence double.

Genuinely out of reach here: the driver-level half of render(). The shader
compile and the vertex upload inside Cura's OpenGL renderer need a real GL
context, so those calls are asserted against a recording factory; frames
are the offscreen harness's job, not a unit test's.

Measured on the last run — plugins/BedMeshSceneNode.py 99%,
plugins/BedMeshPresenter.py 100%. Leftovers, both defensive code that the
public entry points cannot reach:

  BedMeshSceneNode.py:87   the trailing `return [*stops[-1][1], alpha]`:
                           `t` is clamped to [0, 1] and the last stop's right
                           edge is exactly 1.0, so the loop always returns
                           from inside.
  BedMeshSceneNode.py:277  the `length <= 1e-9` guard in
                           append_boundary_segment: updateMesh rejects
                           x_max <= x_min and columns < 2, so every ribbon
                           segment has a positive length.
"""
from __future__ import annotations

import sys
import time
import unittest
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from plugins.MonitorFormatting import parse_bed_mesh
from qt_runtime_support import QT_AVAILABLE, Preferences, runtime

try:  # the stdlib-only host suite runs without numpy
    import numpy
except ImportError:
    numpy = None

STATUS = {
    "profile_name": "default",
    "mesh_min": [40.0, 35.0],
    "mesh_max": [210.0, 215.0],
    "mesh_matrix": [
        [-0.100, 0.000, 0.100],
        [-0.050, 0.020, 0.080],
        [0.000, 0.040, 0.050],
    ],
}
SNAPSHOT = parse_bed_mesh(STATUS)
# A second mesh with a much narrower height range, for the window re-clamp.
NARROW_SNAPSHOT = parse_bed_mesh(dict(
    STATUS,
    mesh_matrix=[[-0.010, 0.000, 0.010], [-0.005, 0.002, 0.008], [0.000, 0.004, 0.005]],
))
# A zero-range mesh: the range filter has nothing to narrow to.
FLAT_SNAPSHOT = dict(SNAPSHOT, profile="flat", values=(0.0,) * 9,
                     minimum=0.0, maximum=0.0, range=0.0)

# The 250 mm plate the node tests draw on: the mesh bounds sit well inside it,
# so each axis grows one ring out to the true Cura bed edges.
PLATE = 250.0
SURFACE_VERTICES = 25  # 5x5: the 3x3 mesh plus one extended ring per side


def _module(name, **attrs):
    parent = name.rpartition(".")[0]
    if parent and parent not in sys.modules:
        _module(parent)
    module = ModuleType(name)
    module.__path__ = []
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def install_scene_stubs():
    """The UM modules runtime() leaves out: the node's base class, the mesh
    container, the GL factory and the shader path lookup. They record what the
    node asks of its host."""
    calls = SimpleNamespace(
        nodes=[], parents=[], shaders=[],
        shader_path=Mock(return_value="/stub/default.shader"),
    )

    class MeshData:
        def __init__(self, vertices=None, indices=None, colors=None):
            self.vertices, self.indices, self.colors = vertices, indices, colors

    class SceneNode:
        def __init__(self, parent=None, name=None, node_id=None, **_ignored):
            self.name, self.node_id, self.parent = name, node_id, parent
            self.mesh_data = None
            self.visible = True
            self.selectable = None
            self.calculate_bounding_box = None
            calls.nodes.append(self)

        def setSelectable(self, value): self.selectable = value
        def setCalculateBoundingBox(self, value): self.calculate_bounding_box = value
        def setMeshData(self, value): self.mesh_data = value
        def getMeshData(self): return self.mesh_data
        def setVisible(self, value): self.visible = bool(value)
        def isVisible(self): return self.visible
        def setParent(self, parent):
            previous, self.parent = self.parent, parent
            calls.parents.append(parent)
            return previous
        def getParent(self): return self.parent
        def getName(self): return self.name

    class GL:
        def createShaderProgram(self, path):
            calls.shaders.append(path)
            return "shader-program"

    _module("UM.Mesh.MeshData", MeshData=MeshData)
    _module("UM.Scene.SceneNode", SceneNode=SceneNode)
    _module("UM.View.GL.OpenGL", OpenGL=SimpleNamespace(getInstance=lambda: GL()))
    sys.modules["UM.Resources"].Resources.getPath = calls.shader_path
    sys.modules["UM.Resources"].Resources.Shaders = "shaders"
    return calls


if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal

    class _Scene(QObject):
        sceneChanged = pyqtSignal(object)

        def __init__(self, root):
            super().__init__()
            self._root = root

        def getRoot(self): return self._root

    class _Cura(QObject):
        """CuraIntegration's seam as the presenter declares it: the stage edge,
        the owned scene decoration and the redraw signal."""

        changed = pyqtSignal()

        def __init__(self, scene, preview_active=True):
            super().__init__()
            self._scene = scene
            self.preview_active = preview_active
            self.controller = SimpleNamespace(getScene=lambda: scene)

        @contextmanager
        def decorating_scene(self):
            yield self._scene.getRoot()

    class _Presentation(QObject):
        """PreviewPresentation's published surface and user intents."""

        controlsChanged = pyqtSignal()
        bedMeshVisibilityRequested = pyqtSignal(bool)
        bedMeshExaggerationRequested = pyqtSignal(float)

        def __init__(self):
            super().__init__()
            self.published = {}

        def publish(self, values):
            self.published.update(values)


class _Persistence:
    """The settings-document facade: the global section is the presenter's home
    after the re-point, the preferences carry the pre-migration values."""

    def __init__(self, document=None):
        self.document = document or {}
        self.global_writes = []

    def settings_document(self):
        return self.document

    def set_global(self, patch):
        self.global_writes.append(dict(patch))
        self.document.setdefault("global", {}).update(patch)
        return True


@unittest.skipUnless(QT_AVAILABLE and numpy is not None,
                     "the scene node needs numpy, the presenter needs PyQt6")
class BedMeshHarness(unittest.TestCase):
    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.stubs = install_scene_stubs()
        # The synthetic host package caches submodules process-wide; without the
        # pops a module stays bound to an earlier test's stub classes.
        sys.modules.pop("_moonraker_runtime_test.BedMeshSceneNode", None)
        sys.modules.pop("_moonraker_runtime_test.BedMeshPresenter", None)
        self.node_class = self.qt.load("BedMeshSceneNode").BedMeshSceneNode
        self.presenter_class = self.qt.load("BedMeshPresenter").BedMeshPresenter

    def node(self):
        return self.node_class()

    def presenter(self, *, preferences=None, persistence=None, preview_active=True):
        application = self.qt.Application(preferences=preferences)
        scene = _Scene(application.getController().getScene().getRoot())
        cura, presentation = _Cura(scene, preview_active=preview_active), _Presentation()
        instance = self.presenter_class(application, cura, presentation, None, persistence)
        self.addCleanup(instance.close)
        redraws = []
        scene.sceneChanged.connect(redraws.append)
        return SimpleNamespace(presenter=instance, application=application, cura=cura,
                               presentation=presentation, scene=scene, root=scene.getRoot(),
                               redraws=redraws, published=presentation.published)


class BedMeshSceneNodeTests(BedMeshHarness):
    def surface(self, node, **kwargs):
        settings = dict(machine_width=PLATE, machine_depth=PLATE,
                        center_is_zero=False, exaggeration=20.0)
        settings.update(kwargs)
        self.assertTrue(node.updateMesh(SNAPSHOT, **settings))
        return node.getMeshData()

    def test_scene_node_is_not_selectable_and_never_computes_a_bounding_box(self):
        node = self.node()
        self.assertEqual(node.getName(), "Moonraker bed mesh")
        self.assertIs(node.selectable, False)
        self.assertIs(node.calculate_bounding_box, False)

    def test_surface_extends_to_the_bed_edges_with_klippers_own_clamp(self):
        mesh = self.surface(self.node())
        # 3x3 mesh + the extended ring = 5x5, plus the 8 ribbon segments.
        self.assertEqual(len(mesh.vertices), SURFACE_VERTICES + 32)
        self.assertEqual(len(mesh.indices), 4 * 4 * 2 + 16)
        self.assertEqual(len(mesh.colors), len(mesh.vertices))

        bed_corner, mesh_corner = mesh.vertices[0], mesh.vertices[1 * 5 + 1]
        # Vertex 0 is printer (0, 0) — the front-left plate corner in Cura's scene.
        self.assertAlmostEqual(float(bed_corner[0]), -PLATE / 2, places=4)
        self.assertAlmostEqual(float(bed_corner[2]), PLATE / 2, places=4)
        self.assertAlmostEqual(float(mesh_corner[0]), 40.0 - PLATE / 2, places=4)
        self.assertAlmostEqual(float(mesh_corner[2]), PLATE / 2 - 35.0, places=4)

        # Outside the probed bounds the boundary cell's value is continued — no
        # made-up slope, no drop to zero.
        self.assertAlmostEqual(float(bed_corner[1]), float(mesh_corner[1]), places=4)
        self.assertAlmostEqual(float(bed_corner[1]), 0.035 + (-0.100) * 20.0, places=4)
        # ...and the extension is marked as unmeasured data.
        self.assertAlmostEqual(float(mesh.colors[0][3]), 0.28, places=5)
        self.assertAlmostEqual(float(mesh.colors[1 * 5 + 1][3]), 0.58, places=5)
        for channel in range(3):
            self.assertAlmostEqual(float(mesh.colors[0][channel]),
                                   float(mesh.colors[1 * 5 + 1][channel]), places=5)

    def test_ribbon_marks_the_probed_bounds_exactly_and_rides_above_the_surface(self):
        mesh = self.surface(self.node())
        ribbon = mesh.vertices[SURFACE_VERTICES:]
        self.assertEqual(len(ribbon), 32)
        self.assertEqual(len(mesh.colors[SURFACE_VERTICES:]), 32)
        self.assertEqual(int(mesh.indices.min()), 0)
        self.assertEqual(int(mesh.indices.max()), SURFACE_VERTICES + 32 - 1)

        # The ribbon follows the Klipper bounds (plus half its own width), not
        # the plate edges the surface was extended to.
        printer_x = [float(vertex[0]) + PLATE / 2 for vertex in ribbon]
        printer_y = [PLATE / 2 - float(vertex[2]) for vertex in ribbon]
        self.assertGreaterEqual(min(printer_x), 40.0 - 1.4)
        self.assertLessEqual(max(printer_x), 210.0 + 1.4)
        self.assertGreaterEqual(min(printer_y), 35.0 - 1.4)
        self.assertLessEqual(max(printer_y), 215.0 + 1.4)

        for colour in mesh.colors[SURFACE_VERTICES:]:
            for channel in range(3):
                self.assertAlmostEqual(float(colour[channel]), (1.0, 0.353, 0.0)[channel], places=5)
            self.assertAlmostEqual(float(colour[3]), 0.94, places=5)
        # One ribbon corner sits on the probe-boundary value, lifted clear of it.
        self.assertAlmostEqual(float(ribbon[1][1]) - float(mesh.vertices[6][1]), 0.09, places=4)

    def test_centred_machine_puts_the_printer_origin_at_the_scene_centre(self):
        node = self.node()
        self.assertTrue(node.updateMesh(SNAPSHOT, 200.0, 150.0, True))
        mesh = node.getMeshData()
        # Centred: printer x is scene x, and printer Y runs into -Z.
        self.assertAlmostEqual(float(mesh.vertices[0][0]), -100.0, places=4)
        self.assertAlmostEqual(float(mesh.vertices[0][2]), 75.0, places=4)

    def test_axis_with_bed_edges_widens_or_bounds_each_side(self):
        axis = self.node_class._axis_with_bed_edges
        self.assertEqual(axis(40.0, 210.0, 3, 0.0, 250.0), [0.0, 40.0, 125.0, 210.0, 250.0])
        self.assertEqual(axis(40.0, 210.0, 3, 40.0, 210.0), [40.0, 125.0, 210.0])
        # A Cura profile narrower than the configured mesh stays bounded to the
        # visible plate rather than drawing outside it.
        self.assertEqual(axis(40.0, 210.0, 3, 60.0, 180.0), [60.0, 125.0, 180.0])
        self.assertEqual(axis(40.0, 210.0, 3, -100.0, 100.0), [-100.0, 40.0, 125.0, 100.0])

    def test_sampling_clamps_like_klipper_instead_of_sloping(self):
        matrix = [[0.0, 0.1], [0.2, 0.3]]
        sample = self.node_class._sample_matrix
        bounds = (0.0, 100.0, 0.0, 100.0)
        self.assertAlmostEqual(sample(matrix, -50.0, -50.0, *bounds), 0.0)
        self.assertAlmostEqual(sample(matrix, 150.0, 150.0, *bounds), 0.3)
        # Outside one axis only: the boundary edge's interpolated value continues.
        self.assertAlmostEqual(sample(matrix, -50.0, 50.0, *bounds), 0.1)
        self.assertAlmostEqual(sample(matrix, 150.0, 0.0, *bounds), 0.1)
        # Inside: plain bilinear interpolation.
        rise = [[0.0, 1.0], [0.0, 1.0]]
        self.assertAlmostEqual(sample(rise, 25.0, 0.0, *bounds), 0.25)
        self.assertAlmostEqual(sample(rise, 50.0, 50.0, *bounds), 0.5)

    def test_extrapolation_segment_returns_the_cell_and_its_fraction(self):
        segment = self.node_class._extrapolation_segment
        self.assertEqual(segment(0.5, 1), (0, 0.0))
        self.assertEqual(segment(-2.0, 3), (0, -2.0))
        self.assertEqual(segment(0.0, 3), (0, 0.0))
        self.assertEqual(segment(1.5, 3), (1, 0.5))
        # At or past the last cell the fraction is left unconstrained, so the
        # boundary value is continued rather than sloped.
        self.assertEqual(segment(2.0, 3), (1, 1.0))
        self.assertEqual(segment(4.0, 3), (1, 3.0))

    def test_colour_walks_the_rainbow_stops_and_the_alpha(self):
        colour = self.node_class._colour
        for value, expected in ((-1.0, (0.10, 0.28, 0.95)), (-0.5, (0.00, 0.72, 1.00)),
                                (0.0, (0.20, 0.86, 0.38)), (0.5, (1.00, 0.82, 0.12)),
                                (1.0, (0.92, 0.16, 0.12))):
            measured = colour(value, -1.0, 1.0)
            for channel in range(3):
                self.assertAlmostEqual(measured[channel], expected[channel], places=5)
            self.assertAlmostEqual(measured[3], 0.58, places=5)
            self.assertAlmostEqual(colour(value, -1.0, 1.0, extrapolated=True)[3], 0.28, places=5)
        # A value past the mesh maximum clamps to the top of the scale, and a
        # zero-range mesh must not divide by zero.
        self.assertAlmostEqual(colour(9.0, -1.0, 1.0)[0], 0.92, places=5)
        self.assertAlmostEqual(colour(0.0, 0.0, 0.0)[1], 0.86, places=5)

    def test_exaggeration_flattens_at_zero_and_clamps_at_the_ceiling(self):
        node = self.node()
        self.assertAlmostEqual(
            float(self.surface(node, exaggeration=0.0).vertices[6][1]), 0.035, places=4)
        raised = self.surface(node, exaggeration=99999.0)
        self.assertAlmostEqual(float(raised.vertices[6][1]), 0.035 - 0.100 * 1000.0, places=3)

    def test_heightmap_window_greys_the_cells_outside_it(self):
        grey = (0.541, 0.561, 0.596)
        mesh = self.surface(self.node(), low=-0.02, high=0.03)
        colours = mesh.colors
        self.assertEqual(tuple(round(float(c), 3) for c in colours[6][:3]), grey)
        self.assertAlmostEqual(float(colours[6][3]), 0.58, places=5)
        self.assertEqual(tuple(round(float(c), 3) for c in colours[0][:3]), grey)
        self.assertAlmostEqual(float(colours[0][3]), 0.28, places=5)
        # The mesh centre sits inside the window, so it keeps the rainbow.
        self.assertAlmostEqual(float(colours[12][3]), 0.58, places=5)
        self.assertNotEqual(tuple(round(float(c), 3) for c in colours[12][:3]), grey)
        # Half a window is no window: the range filter would grey everything.
        half = self.surface(self.node(), low=-0.02)
        self.assertEqual(tuple(round(float(c), 3) for c in half.colors[6][:3]),
                         (0.100, 0.280, 0.950))

    def test_unusable_snapshots_and_bounds_clear_the_surface(self):
        node = self.node()
        self.surface(node)
        broken = [
            {},                                             # missing bounds
            dict(SNAPSHOT, rows="wide"),                    # unreadable row count
            dict(SNAPSHOT, values=(0.0,) * 8),              # short of rows*columns
            dict(SNAPSHOT, rows=1, columns=1, values=(0.0,)),  # fewer than two cells
            dict(SNAPSHOT, xMax=40.0),                      # empty x span
            dict(SNAPSHOT, yMax=35.0),                      # empty y span
        ]
        for status in broken:
            self.assertFalse(node.updateMesh(status, PLATE, PLATE, False))
            self.assertIsNone(node.getMeshData())
            self.assertFalse(node.isVisible())
        self.assertFalse(node.updateMesh(SNAPSHOT, 0.0, PLATE, False))  # no plate to draw on
        self.assertIsNone(node.getMeshData())

    def test_clear_drops_the_mesh_and_hides_the_node(self):
        node = self.node()
        self.surface(node)
        node.setVisible(True)
        node.clear()
        self.assertIsNone(node.getMeshData())
        self.assertFalse(node.isVisible())

    def test_render_queues_the_mesh_transparently_with_a_cached_shader(self):
        node, renderer = self.node(), Mock()
        node.setVisible(True)
        self.assertTrue(node.render(renderer))
        renderer.queueNode.assert_not_called()  # nothing to draw yet

        self.surface(node)
        self.assertTrue(node.render(renderer))
        self.stubs.shader_path.assert_called_once_with("shaders", "default.shader")
        self.assertEqual(self.stubs.shaders, ["/stub/default.shader"])
        self.assertEqual(renderer.queueNode.call_args.args[0], node)
        self.assertEqual(renderer.queueNode.call_args.kwargs,
                         {"shader": "shader-program", "transparent": True,
                          "backface_cull": False, "sort": -4})

        self.assertTrue(node.render(renderer))
        self.assertEqual(len(self.stubs.shaders), 1)  # compiled once, reused per frame

        node.setVisible(False)
        renderer.queueNode.reset_mock()
        self.assertTrue(node.render(renderer))
        renderer.queueNode.assert_not_called()


class BedMeshPresenterTests(BedMeshHarness):
    def test_preferences_seed_the_defaults_and_the_snapshot_stays_read_only(self):
        setup = self.presenter(preferences=Preferences())
        self.assertTrue(setup.presenter.visible)
        self.assertEqual(setup.published["bedMeshExaggeration"], 20.0)
        self.assertEqual(setup.published["bedMeshAvailable"], False)
        self.assertEqual(setup.published["bedMeshRangeText"], "")
        self.assertEqual(setup.published["bedMeshThresholdLow"], 0.0)
        self.assertEqual(setup.published["bedMeshThresholdHigh"], 0.0)
        self.assertEqual(dict(setup.presenter.snapshot), {})
        with self.assertRaises(TypeError):
            setup.presenter.snapshot["profile"] = "default"

    def test_a_pre_migration_preference_accepts_the_stored_forms(self):
        key = self.presenter_class.PREF_KEY
        for stored, expected in ((True, True), (False, False), ("false", False),
                                 ("off", False), ("no", False), ("0", False), ("TRUE", True)):
            setup = self.presenter(preferences=Preferences({key: stored}))
            self.assertEqual(setup.presenter.visible, expected, stored)

    def test_the_settings_document_wins_over_the_pre_migration_preferences(self):
        setup = self.presenter(
            preferences=Preferences({self.presenter_class.PREF_KEY: True,
                                     self.presenter_class.EXAGGERATION_PREF_KEY: 20.0}),
            persistence=_Persistence({"global": {"bedMeshVisible": False,
                                                 "bedMeshExaggeration": 100.0}}))
        self.assertFalse(setup.presenter.visible)
        self.assertEqual(setup.published["bedMeshExaggeration"], 100.0)
        # A document value is clamped exactly like the slider's.
        clamped = self.presenter(
            persistence=_Persistence({"global": {"bedMeshExaggeration": 5000}}))
        self.assertEqual(clamped.published["bedMeshExaggeration"], 1000.0)

    def test_unreadable_stored_exaggeration_falls_back_to_the_default(self):
        setup = self.presenter(
            preferences=Preferences({self.presenter_class.EXAGGERATION_PREF_KEY: "wide"}))
        self.assertEqual(setup.published["bedMeshExaggeration"], 20.0)
        migrated = self.presenter(
            persistence=_Persistence({"global": {"bedMeshExaggeration": "wide"}}))
        self.assertEqual(migrated.published["bedMeshExaggeration"], 20.0)

    def test_update_builds_the_surface_on_the_scene_root_and_publishes_the_readouts(self):
        setup = self.presenter()
        changes = []
        setup.presenter.changed.connect(lambda: changes.append(1))
        setup.presenter.update(SNAPSHOT)

        node = self.stubs.nodes[-1]
        self.assertIs(node.getParent(), setup.root)
        self.assertEqual(setup.redraws, [node])  # the scene repaints for the new node
        # The plate comes from Cura's container stack, so printer (0, 0) lands on
        # the bed corner of the harness's 200 mm machine.
        mesh = node.getMeshData()
        self.assertAlmostEqual(float(mesh.vertices[0][0]), -100.0, places=4)
        self.assertAlmostEqual(float(mesh.vertices[0][2]), 100.0, places=4)
        self.assertTrue(node.isVisible())
        self.assertEqual(len(changes), 1)

        self.assertTrue(setup.published["bedMeshAvailable"])
        self.assertEqual(setup.published["bedMeshRangeText"], "0.200 mm range")
        self.assertEqual(setup.published["bedMeshMinimumText"], "-0.100 mm")
        self.assertEqual(setup.published["bedMeshMaximumText"], "+0.100 mm")
        self.assertAlmostEqual(setup.published["bedMeshThresholdLow"], -0.1)
        self.assertAlmostEqual(setup.published["bedMeshThresholdHigh"], 0.1)
        self.assertEqual(setup.published["bedMeshVisible"], True)
        self.assertEqual(self.stubs.parents, [setup.root])  # parented once, not per poll

    def test_a_repeated_poll_neither_rebuilds_nor_republishes(self):
        setup = self.presenter()
        setup.presenter.update(SNAPSHOT)
        mesh = self.stubs.nodes[-1].getMeshData()
        changes = []
        setup.presenter.changed.connect(lambda: changes.append(1))
        setup.presenter.update(dict(SNAPSHOT))
        setup.presenter.update({**SNAPSHOT, "poll": 42})  # the live status feeds extra keys
        self.assertEqual(changes, [])
        self.assertIs(self.stubs.nodes[-1].getMeshData(), mesh)
        self.assertEqual(len(self.stubs.nodes), 1)

    def test_visibility_toggles_the_surface_and_writes_the_settings_document(self):
        persistence = _Persistence()
        setup = self.presenter(persistence=persistence)
        setup.presenter.update(SNAPSHOT)
        node = self.stubs.nodes[-1]

        setup.presenter.set_visible(False)
        self.assertFalse(setup.presenter.visible)
        self.assertFalse(node.isVisible())
        self.assertEqual(persistence.global_writes[-1], {"bedMeshVisible": False})
        self.assertEqual(self.stubs.parents, [setup.root])  # a toggle re-uses the node

        setup.presenter.set_visible(True)
        self.assertTrue(node.isVisible())
        self.assertEqual(persistence.global_writes[-1], {"bedMeshVisible": True})

        # Without the settings document the preference is still written.
        plain = self.presenter(preferences=Preferences())
        plain.presenter.set_visible(False)
        self.assertEqual(plain.application.getPreferences().values[self.presenter_class.PREF_KEY],
                         False)

    def test_the_surface_needs_the_preview_stage_a_snapshot_and_the_visible_flag(self):
        setup = self.presenter(preview_active=False)
        setup.presenter.update(SNAPSHOT)
        node = self.stubs.nodes[-1]
        self.assertFalse(node.isVisible())  # Cura's Preview stage owns the 3D scene

        setup.cura.preview_active = True
        setup.cura.changed.emit()  # the stage edge re-evaluates the gate
        self.assertTrue(node.isVisible())

        setup.presenter.set_visible(False)
        setup.presenter.set_visible(True)
        self.assertTrue(node.isVisible())
        self.assertEqual(setup.redraws[-1], node)

    def test_the_range_window_clamps_to_the_mesh_and_publishes_its_twins(self):
        setup = self.presenter()
        setup.presenter.update(SNAPSHOT)
        setup.presenter.set_thresholds(-5.0, 5.0)
        self.assertEqual((setup.published["bedMeshThresholdLow"],
                          setup.published["bedMeshThresholdHigh"]), (-0.1, 0.1))
        # The slider can hand the ends over reversed.
        setup.presenter.set_thresholds(0.05, -0.05)
        self.assertEqual((setup.published["bedMeshThresholdLow"],
                          setup.published["bedMeshThresholdHigh"]), (-0.05, 0.05))

        changes = []
        setup.presenter.changed.connect(lambda: changes.append(1))
        setup.presenter.set_thresholds(-0.05, 0.05)
        self.assertEqual(changes, [])  # an unchanged window is a no-op

        untouched = self.presenter()
        untouched.presenter.set_thresholds(-1.0, 1.0)  # no mesh to filter yet
        self.assertEqual((untouched.published["bedMeshThresholdLow"],
                          untouched.published["bedMeshThresholdHigh"]), (0.0, 0.0))
        flat = self.presenter()
        flat.presenter.update(FLAT_SNAPSHOT)
        flat.presenter.set_thresholds(-1.0, 1.0)  # nothing to narrow on a zero-range mesh
        self.assertEqual((flat.published["bedMeshThresholdLow"],
                          flat.published["bedMeshThresholdHigh"]), (0.0, 0.0))

    def test_a_new_mesh_re_clamps_the_window_and_clearing_drops_it(self):
        setup = self.presenter()
        setup.presenter.update(SNAPSHOT)
        setup.presenter.set_thresholds(0.0, 0.1)
        # A shorter mesh pulls the whole window into the new range.
        setup.presenter.update(NARROW_SNAPSHOT)
        self.assertEqual((setup.published["bedMeshThresholdLow"],
                          setup.published["bedMeshThresholdHigh"]), (0.0, 0.01))
        setup.presenter.clear()
        self.assertEqual((setup.published["bedMeshThresholdLow"],
                          setup.published["bedMeshThresholdHigh"]), (0.0, 0.0))

    def test_the_scale_z_max_slider_clamps_persists_and_rebuilds(self):
        persistence = _Persistence()
        setup = self.presenter(persistence=persistence)
        setup.presenter.update(SNAPSHOT)
        node = self.stubs.nodes[-1]
        self.assertAlmostEqual(float(node.getMeshData().vertices[5][1]),
                               0.035 - 0.100 * 20.0, places=4)

        setup.presenter.set_exaggeration(0.0)  # the slider's low end flattens
        self.assertAlmostEqual(float(node.getMeshData().vertices[5][1]), 0.035, places=4)
        setup.presenter.set_exaggeration(5000.0)
        self.assertEqual(setup.published["bedMeshExaggeration"], 1000.0)
        self.assertAlmostEqual(float(node.getMeshData().vertices[5][1]),
                               0.035 - 0.100 * 1000.0, places=3)
        self.assertEqual(persistence.global_writes[-1], {"bedMeshExaggeration": 1000.0})

        changes = []
        setup.presenter.changed.connect(lambda: changes.append(1))
        setup.presenter.set_exaggeration(1000.0)
        self.assertEqual(changes, [])  # an unchanged slider position is a no-op

        plain = self.presenter(preferences=Preferences())
        plain.presenter.set_exaggeration(50.0)
        self.assertEqual(plain.application.getPreferences().values[
            self.presenter_class.EXAGGERATION_PREF_KEY], 50.0)

    def test_a_failing_render_is_logged_and_throttled_but_never_aborts_following(self):
        from UM.Logger import Logger

        setup = self.presenter()
        instance = setup.presenter
        failure = RuntimeError("no GL context")

        @contextmanager
        def failing():
            raise failure

        setup.cura.decorating_scene = failing
        instance.update(SNAPSHOT)
        self.assertEqual(Logger.log.call_count, 1)
        self.assertEqual(Logger.log.call_args.args[:2],
                         ("w", "Moonraker bed-mesh render failed: %s"))
        self.assertIs(Logger.log.call_args.args[2], failure)
        # The mesh still publishes: a missing renderer must not stop following.
        self.assertTrue(setup.published["bedMeshAvailable"])

        instance.set_visible(False)
        self.assertEqual(Logger.log.call_count, 1)  # throttled inside the window
        module = self.qt.load("BedMeshPresenter")
        with patch.object(module.time, "monotonic", return_value=time.monotonic() + 31.0):
            instance.set_visible(True)
        self.assertEqual(Logger.log.call_count, 2)

    def test_clear_empties_the_readouts_and_keeps_the_node(self):
        setup = self.presenter()
        setup.presenter.update(SNAPSHOT)
        node = self.stubs.nodes[-1]
        setup.presenter.clear()
        self.assertIsNone(node.getMeshData())
        self.assertFalse(node.isVisible())
        self.assertEqual(dict(setup.presenter.snapshot), {})
        self.assertEqual(setup.published["bedMeshAvailable"], False)
        self.assertEqual(setup.published["bedMeshRangeText"], "")
        self.assertEqual(len(self.stubs.nodes), 1)  # the node is reused, never rebuilt

    def test_close_detaches_the_node_and_survives_a_host_without_a_scene(self):
        setup = self.presenter()
        setup.presenter.update(SNAPSHOT)
        node = self.stubs.nodes[-1]
        setup.presenter.close()
        self.assertIsNone(node.getParent())
        redrawn = list(setup.redraws)  # the clear repaints once on the way out

        setup.presenter.close()  # a second teardown is harmless (the disconnects raise)
        setup.presenter.update(SNAPSHOT)
        self.assertEqual(setup.redraws, redrawn)  # nothing re-enters the scene after close

        # A teardown must not explode when the host's scene is already gone.
        gone = self.presenter()
        gone.presenter.update(SNAPSHOT)
        gone.cura.decorating_scene = Mock(side_effect=RuntimeError("scene gone"))
        gone.presenter.close()


if __name__ == "__main__":
    unittest.main()
