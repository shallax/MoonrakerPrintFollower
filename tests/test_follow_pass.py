"""The follow pass: the layer/line attribute baking over Cura's flat
LayerData, and the pass state. numpy-gated — the host test env may
lack it; the container gate has it.
"""
import unittest

try:
    import numpy
    NUMPY = True
except ImportError:
    NUMPY = False


class _FakeLayerData:
    def __init__(self, vertices, indices, colors, counts, extra=None, layers=None):
        self._vertices = numpy.asarray(vertices, dtype=numpy.float32)
        self._indices = numpy.asarray(indices, dtype=numpy.int32)
        self._colors = numpy.asarray(colors, dtype=numpy.float32)
        self._counts = counts
        self._extra = extra or {}
        self._layers = layers or {}

    def getVertices(self):
        return self._vertices

    def getIndices(self):
        return self._indices

    def getColors(self):
        return self._colors

    def getElementCounts(self):
        return self._counts

    def getLayer(self, layer):
        return self._layers.get(layer)

    def attributeNames(self):
        return sorted(self._extra)

    def getAttribute(self, name):
        return self._extra[name]


@unittest.skipUnless(NUMPY, "numpy not available")
class FollowMeshBakeTests(unittest.TestCase):
    class _FakeMeshData:
        # The UM-free stand-in for MeshData: records what the bake
        # passes to it (the real UM MeshData comes from FollowPass).
        def __init__(self, vertices=None, indices=None, colors=None, attributes=None):
            self.vertices = vertices
            self.indices = indices
            self.colors = colors
            self.attributes = attributes

        def getAttribute(self, name):
            return self.attributes[name]

        def getVertices(self):
            return self.vertices

        def getIndices(self):
            return self.indices

    def _bake(self, counts):
        from plugins.FollowMesh import build_follow_mesh
        # 8 vertices, 8 indices: layers 1 and 3 with two lines each.
        vertices = numpy.zeros((8, 3), numpy.float32)
        indices = numpy.arange(8, dtype=numpy.int32)
        colors = numpy.ones((8, 4), numpy.float32)
        extra = {
            "extruder": {"opengl_type": "float", "opengl_name": "a_extruder",
                         "value": numpy.zeros(8, numpy.float32)},
            "line_type": {"opengl_type": "float", "opengl_name": "a_line_type",
                          "value": numpy.ones(8, numpy.float32)},
        }
        layer_data = _FakeLayerData(vertices, indices, colors, counts, extra)
        return build_follow_mesh(layer_data, mesh_factory=self._FakeMeshData), layer_data

    def test_bakes_layer_and_line_attributes(self):
        mesh, layer_data = self._bake({1: 4, 3: 4})
        self.assertIsNotNone(mesh)
        layer_attr = mesh.getAttribute("layer")
        line_attr = mesh.getAttribute("line")
        self.assertEqual(layer_attr["opengl_name"], "a_layer")
        self.assertEqual(line_attr["opengl_name"], "a_line")
        self.assertEqual(list(layer_attr["value"]), [1, 1, 1, 1, 3, 3, 3, 3])
        self.assertEqual(list(line_attr["value"]), [0, 0, 1, 1, 0, 0, 1, 1])

    def test_references_the_source_arrays(self):
        mesh, layer_data = self._bake({1: 4, 3: 4})
        self.assertTrue(numpy.shares_memory(mesh.getVertices(), layer_data.getVertices()))
        self.assertTrue(numpy.shares_memory(mesh.getIndices(), layer_data.getIndices()))
        self.assertTrue(numpy.shares_memory(mesh.getAttribute("extruder")["value"],
                                            layer_data.getAttribute("extruder")["value"]))

    def test_vertices_beyond_the_counts_table_never_render(self):
        mesh, layer_data = self._bake({1: 4})
        layer_attr = mesh.getAttribute("layer")
        self.assertEqual(list(layer_attr["value"]), [1, 1, 1, 1, 1 << 30, 1 << 30, 1 << 30, 1 << 30])

    def test_bake_fails_cleanly_without_geometry(self):
        from plugins.FollowMesh import build_follow_mesh
        layer_data = _FakeLayerData([], [], [], {})
        self.assertIsNone(build_follow_mesh(layer_data, mesh_factory=self._FakeMeshData))
        layer_data = _FakeLayerData(numpy.zeros((4, 3)), numpy.arange(4), numpy.ones((4, 4)), {})
        self.assertIsNone(build_follow_mesh(layer_data, mesh_factory=self._FakeMeshData))


try:
    import UM  # noqa: F401
    UM_AVAILABLE = True
except ImportError:
    UM_AVAILABLE = False


class _FakeSimulationPass:
    def __init__(self):
        self.disabled_count = 0
        self.enabled_count = 0
        self.enabled = True

    def isEnabled(self):
        return self.enabled

    def setEnabled(self, enabled):
        self.enabled = enabled
        if enabled:
            self.enabled_count += 1
        else:
            self.disabled_count += 1


class _FakeComposite:
    def __init__(self, bindings):
        self.bindings = list(bindings)

    def getLayerBindings(self):
        return self.bindings

    def setLayerBindings(self, bindings):
        self.bindings = list(bindings)


class _FakeRenderer:
    def __init__(self, bindings=("default", "selection", "simulationview")):
        self.composite = _FakeComposite(bindings)
        self.added = []
        self.removed = []

    def getRenderPass(self, name):
        return self.composite if name == "composite" else None

    def addRenderPass(self, render_pass):
        self.added.append(render_pass)

    def removeRenderPass(self, render_pass):
        self.removed.append(render_pass)


class _FakeRoot:
    pass


class _FakeScene:
    def __init__(self):
        self.root = _FakeRoot()

    def getRoot(self):
        return self.root

    def getActiveCamera(self):
        return object()


class _FakeController:
    def __init__(self):
        self.scene = _FakeScene()

    def getScene(self):
        return self.scene


class _FakeNozzle:
    def __init__(self, mesh=True):
        self.mesh = object() if mesh else None
        self.parent = None
        self.visible = True
        self.positions = []
        self.set_parent_calls = 0

    def getMeshData(self):
        return self.mesh

    def getWorldTransformation(self):
        return object()

    def setPosition(self, position):
        self.positions.append(position)

    def getParent(self):
        return self.parent

    def setParent(self, parent):
        self.parent = parent
        self.set_parent_calls += 1

    def setVisible(self, visible):
        self.visible = visible


class _FakeView:
    def __init__(self, bindings=("default", "selection", "simulationview"), nozzle=None):
        self.renderer = _FakeRenderer(bindings)
        self.simulation_pass = _FakeSimulationPass()
        self.controller = _FakeController()
        self.nozzle = nozzle

    def getRenderer(self):
        return self.renderer

    def getSimulationPass(self):
        return self.simulation_pass

    def getController(self):
        return self.controller

    def getShowTravelMoves(self):
        return True

    def getShowHelpers(self):
        return True

    def getShowSkin(self):
        return True

    def getShowInfill(self):
        return True

    def getNozzleNode(self):
        return self.nozzle


class _FakeBatch:
    def __init__(self):
        self.renders = 0

    def render(self, camera):
        self.renders += 1


class _FakeShader:
    def setUniformValue(self, *args):
        pass


class _FakeNode:
    def getWorldTransformation(self):
        return object()

    def getWorldPosition(self):
        from UM.Math.Vector import Vector
        return Vector(0, 0, 0)


@unittest.skipUnless(UM_AVAILABLE and NUMPY, "UM and numpy required")
class FollowPassLifecycleTests(unittest.TestCase):
    """The lifecycle contract (the review's list): uniform ticks never
    rebuild the mesh, renders reuse the one batch, attach/detach
    disable and restore Cura's pass exactly once, a new layer data
    builds exactly one new mesh, and the toolhead reuses the nozzle's
    geometry."""

    def _pass_with_mesh(self, counts=None, layers=None):
        from plugins.FollowPass import FollowPass
        counts = counts if counts is not None else {1: 4, 3: 4}
        vertices = numpy.zeros((8, 3), numpy.float32)
        indices = numpy.arange(8, dtype=numpy.int32)
        colors = numpy.ones((8, 4), numpy.float32)
        layer_data = _FakeLayerData(vertices, indices, colors, counts, layers=layers)
        follow_pass = FollowPass()
        follow_pass.setFollowView(_FakeView())
        follow_pass.setFollowScene(_FakeNode(), layer_data)
        follow_pass._scene = _FakeScene()
        return follow_pass, layer_data

    def test_uniform_ticks_never_rebuild_the_mesh(self):
        follow_pass, _ = self._pass_with_mesh()
        mesh = follow_pass._mesh
        self.assertIsNotNone(mesh)
        for tick in range(50):
            follow_pass.setFollowState(tick % 5, 0.1 + tick)
        self.assertIs(follow_pass._mesh, mesh)

    def test_repeated_renders_reuse_the_batch(self):
        follow_pass, _ = self._pass_with_mesh()
        fake_batch = _FakeBatch()
        follow_pass._batch = fake_batch
        follow_pass._shader = _FakeShader()
        for _ in range(10):
            follow_pass.render()
        self.assertIs(follow_pass._batch, fake_batch)
        self.assertEqual(fake_batch.renders, 10)

    def test_a_new_layer_data_builds_exactly_one_mesh(self):
        import unittest.mock as mock
        from plugins import FollowPass as module
        follow_pass, first_data = self._pass_with_mesh()
        first_mesh = follow_pass._mesh
        with mock.patch.object(module, "build_follow_mesh", wraps=module.build_follow_mesh) as spy:
            follow_pass.setFollowScene(_FakeNode(), first_data)
            self.assertEqual(spy.call_count, 0)  # the identity cache holds
            second_data = _FakeLayerData(numpy.zeros((8, 3), numpy.float32),
                                         numpy.arange(8, dtype=numpy.int32),
                                         numpy.ones((8, 4), numpy.float32), {1: 4, 3: 4})
            follow_pass.setFollowScene(_FakeNode(), second_data)
            self.assertEqual(spy.call_count, 1)
            self.assertIsNot(follow_pass._mesh, first_mesh)

    def test_toolhead_reuses_the_nozzle_geometry(self):
        follow_pass, _ = self._pass_with_mesh(layers=_polygon_layers())
        follow_pass._nozzle_shader = _FakeShader()
        nozzle = _FakeNozzle()
        follow_pass.setFollowView(_FakeView(nozzle=nozzle))
        follow_pass.setFollowState(1, 2.0)
        follow_pass._render_toolhead(object())
        follow_pass._render_toolhead(object())
        # The nozzle's own mesh object is reused; nothing persistent
        # is constructed per frame.
        self.assertEqual(len(nozzle.positions), 2)

    def test_toolhead_position_changes_with_fractional_path(self):
        follow_pass, _ = self._pass_with_mesh(layers=_polygon_layers())
        follow_pass._nozzle_shader = _FakeShader()
        nozzle = _FakeNozzle()
        follow_pass.setFollowView(_FakeView(nozzle=nozzle))
        follow_pass.setFollowState(1, 1.0)
        follow_pass._render_toolhead(object())
        follow_pass.setFollowState(1, 1.5)
        follow_pass._render_toolhead(object())
        self.assertEqual(len(nozzle.positions), 2)
        first, second = [tuple(round(float(c), 4) for c in position) for position in nozzle.positions]
        # The polygon points are (0,0,0), (1,0,0), (2,0,0): the whole
        # position lands on point 1, and the half-way fraction
        # interpolates exactly between points 1 and 2.
        self.assertEqual(first, (1.0, 0.0, 0.0))
        self.assertEqual(second, (1.5, 0.0, 0.0))

    def test_toolhead_missing_mesh_fails_cleanly(self):
        follow_pass, _ = self._pass_with_mesh(layers=_polygon_layers())
        follow_pass._nozzle_shader = _FakeShader()
        nozzle = _FakeNozzle(mesh=False)
        follow_pass.setFollowView(_FakeView(nozzle=nozzle))
        follow_pass.setFollowState(1, 2.0)
        follow_pass._render_toolhead(object())  # must not raise
        self.assertEqual(len(nozzle.positions), 0)
        self.assertFalse(follow_pass._toolhead_error_logged)

    def test_follow_pass_binds_before_render_and_releases_after(self):
        follow_pass, _ = self._pass_with_mesh()
        follow_pass._shader = _FakeShader()
        batch = _FakeBatch()
        follow_pass._batch = batch
        events = []
        follow_pass.bind = lambda: events.append("bind")
        follow_pass.release = lambda: events.append("release")
        original_render = batch.render
        batch.render = lambda camera: events.append("draw")
        try:
            follow_pass.render()
        finally:
            batch.render = original_render
        self.assertEqual(events, ["bind", "draw", "release"])

    def test_follow_pass_releases_even_when_batch_render_raises(self):
        follow_pass, _ = self._pass_with_mesh()
        follow_pass._shader = _FakeShader()
        batch = _FakeBatch()
        follow_pass._batch = batch
        released = []

        def broken_render(camera):
            raise RuntimeError("boom")

        batch.render = broken_render
        follow_pass.bind = lambda: None
        follow_pass.release = lambda: released.append(True)
        with self.assertRaises(RuntimeError):
            follow_pass.render()
        self.assertEqual(released, [True])


def _polygon_layers():
    class _FakePolygon:
        def __init__(self):
            self.data = numpy.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]], numpy.float32)

    class _FakePolygonsLayer:
        def __init__(self):
            self.polygons = [_FakePolygon()]

    return {1: _FakePolygonsLayer(), 3: _FakePolygonsLayer()}


@unittest.skipUnless(UM_AVAILABLE and NUMPY, "UM and numpy required")
class FollowPassControllerTests(unittest.TestCase):
    """Attach/detach/shutdown against fake renderer and compositor
    objects (the controller touches only the public APIs)."""

    def setUp(self):
        import plugins.FollowPassController as controller
        self.controller = controller
        self.view = _FakeView()
        self._original_active = controller.ACTIVE
        self._original_bindings = controller._ORIGINAL_BINDINGS
        self._original_sim = controller._SIMULATION_PASS
        controller.ACTIVE = None
        controller._ORIGINAL_BINDINGS = None
        controller._SIMULATION_PASS = None
        controller._CAPABLE = True

    def tearDown(self):
        self.controller.ACTIVE = self._original_active
        self.controller._ORIGINAL_BINDINGS = self._original_bindings
        self.controller._SIMULATION_PASS = self._original_sim
        self.controller._CAPABLE = True

    def _patch_layer_data(self):
        import unittest.mock as mock
        vertices = numpy.zeros((8, 3), numpy.float32)
        indices = numpy.arange(8, dtype=numpy.int32)
        colors = numpy.ones((8, 4), numpy.float32)
        layer_data = _FakeLayerData(vertices, indices, colors, {1: 4, 3: 4})
        return mock.patch.object(self.controller, "_find_layer_data",
                                 return_value=(_FakeNode(), layer_data))

    def test_attach_disables_the_simulation_pass_and_swaps_the_binding(self):
        with self._patch_layer_data():
            self.assertTrue(self.controller.attach(self.view))
        self.assertEqual(self.view.simulation_pass.disabled_count, 1)
        self.assertEqual(self.view.renderer.composite.bindings,
                         ["default", "selection", self.controller.PASS_NAME])
        self.assertIsNotNone(self.controller.ACTIVE)
        self.assertTrue(self.controller.ACTIVE.isEnabled())

    def test_updates_never_reenable_the_simulation_pass(self):
        with self._patch_layer_data():
            self.assertTrue(self.controller.attach(self.view))
        for tick in range(20):
            self.controller.update(tick % 3, 0.5, toolhead=True)
        self.assertEqual(self.view.simulation_pass.disabled_count, 1)
        self.assertEqual(self.view.simulation_pass.enabled_count, 0)

    def test_detach_restores_the_bindings_and_the_simulation_pass(self):
        with self._patch_layer_data():
            self.assertTrue(self.controller.attach(self.view))
        self.controller.detach(self.view)
        self.assertEqual(self.view.simulation_pass.enabled_count, 1)
        self.assertEqual(self.view.renderer.composite.bindings,
                         ["default", "selection", "simulationview"])
        self.assertFalse(self.controller.ACTIVE.isEnabled())
        self.assertIsNone(self.controller._ORIGINAL_BINDINGS)
        self.assertIsNone(self.controller._SIMULATION_PASS)

    def test_shutdown_removes_the_pass_from_the_renderer(self):
        with self._patch_layer_data():
            self.assertTrue(self.controller.attach(self.view))
        active = self.controller.ACTIVE
        self.controller.shutdown()
        self.assertIn(active, self.view.renderer.removed)
        self.assertIsNone(self.controller.ACTIVE)
        self.assertIsNone(self.controller._ORIGINAL_BINDINGS)
        self.assertIsNone(self.controller._SIMULATION_PASS)

    def _attach_with_nozzle(self, nozzle):
        self.view = _FakeView(nozzle=nozzle)
        with self._patch_layer_data():
            self.assertTrue(self.controller.attach(self.view))
        return nozzle

    def test_attach_ensures_nozzle_is_parented_to_scene_root(self):
        nozzle = self._attach_with_nozzle(_FakeNozzle())
        self.assertIs(nozzle.parent, self.view.controller.scene.getRoot())

    def test_attach_hides_nozzle_from_normal_scene_rendering(self):
        nozzle = self._attach_with_nozzle(_FakeNozzle())
        self.assertFalse(nozzle.visible)

    def test_attach_does_not_reparent_nozzle_when_already_correct(self):
        nozzle = _FakeNozzle()
        self.view = _FakeView(nozzle=nozzle)
        nozzle.setParent(self.view.controller.scene.getRoot())
        self.assertEqual(nozzle.set_parent_calls, 1)
        with self._patch_layer_data():
            self.assertTrue(self.controller.attach(self.view))
        self.assertEqual(nozzle.set_parent_calls, 1)  # untouched
        self.assertIs(nozzle.parent, self.view.controller.scene.getRoot())

    def test_updates_do_not_touch_nozzle_parent(self):
        nozzle = self._attach_with_nozzle(_FakeNozzle())
        parent = nozzle.parent
        for tick in range(10):
            self.controller.update(tick % 3, 0.5, toolhead=True)
        self.assertIs(nozzle.parent, parent)

    def test_detach_leaves_nozzle_parented_for_native_simulation_pass(self):
        nozzle = self._attach_with_nozzle(_FakeNozzle())
        root = self.view.controller.scene.getRoot()
        self.controller.detach(self.view)
        self.assertIs(nozzle.parent, root)


if __name__ == "__main__":
    unittest.main()
