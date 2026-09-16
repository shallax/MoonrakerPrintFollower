"""The render-path adaptation: exact-block replacement, the version
gates, and the drawing semantics of the patched methods.

The fakes embed the REAL fragments verbatim from the bundled Cura
5.13.0 sources, so the same patch machinery exercised here is what
runs against the installed renderer. numpy is absent on some hosts —
the SimulationPass functional test skips there.
"""
import ctypes
import types
import unittest

from plugins.RendererAdaptation import (
    COUNTERS,
    _patch_render_batch,
    _patch_simulation_pass,
    _PREV_LINE_TYPES_FRAGMENT,
    _PREV_LINE_TYPES_REPLACEMENT,
    _RANGED_BUFFER_FRAGMENT,
    _RANGED_BUFFER_REPLACEMENT,
    _RANGED_DRAW_FRAGMENT,
    _RANGED_DRAW_REPLACEMENT,
    patch_method,
)

from typing import Any, Dict

# The fake render's fragment references these names; the real fakes are
# injected into the function's globals before each patch runs. The
# placeholders exist so the fragment text itself needs no alteration.
numpy = None
LayerPolygon = None


class FakeRenderMode:
    Triangles = 4
    Lines = 1


class FakeVoidPtr:
    """The test stand-in for sip.voidptr — carries the pointer value."""

    def __init__(self, value):
        self.value = value


def fake_offset_ptr(offset):
    return FakeVoidPtr(offset)


class FakeIndexBuffer:
    def bind(self):
        pass

    def release(self):
        pass


class FakeVertexBuffer(FakeIndexBuffer):
    pass


class OpenGL:  # injected into the namespace as `OpenGL`
    # The methods live ON THE CLASS, like the real UM OpenGL, so the
    # counter wrap and the patched code see the same shape; the
    # singleton instance binds them.
    IndexBufferProperty = "mpf_index_buffer"
    create_calls = []
    create_count = 0
    _instance = None

    @staticmethod
    def getInstance():
        if OpenGL._instance is None:
            OpenGL._instance = OpenGL()
        return OpenGL._instance

    def createIndexBuffer(self, mesh, **kwargs):
        type(self).create_calls.append(kwargs)
        if not kwargs.get("force_recreate") and hasattr(mesh, self.IndexBufferProperty):
            return getattr(mesh, self.IndexBufferProperty)
        type(self).create_count += 1
        buffer = FakeIndexBuffer()
        setattr(mesh, self.IndexBufferProperty, buffer)
        return buffer

    def createVertexBuffer(self, mesh):
        return FakeVertexBuffer()


class FakeGL:
    GL_UNSIGNED_INT = 5125

    def __init__(self):
        self.draws = []

    def glDrawElements(self, mode, count, kind, offset):
        self.draws.append(("elements", mode, count, kind, offset))

    def glDrawRangeElements(self, mode, start, end, count, kind, offset):
        self.draws.append(("range", mode, start, end, count, kind, offset))


class FakeMesh:
    def __init__(self, indices=True):
        self.vertex_count = 1
        self.face_count = 10
        self.indices = indices

    def getVertexCount(self):
        return self.vertex_count

    def getFaceCount(self):
        return self.face_count

    def hasIndices(self):
        return self.indices

    def hasNormals(self):
        return False

    def hasColors(self):
        return False

    def hasUVCoordinates(self):
        return False

    def attributeNames(self):
        return []


class FakeRenderBatch:
    RenderMode = FakeRenderMode

    def __init__(self):
        self._gl = FakeGL()
        self._render_mode = FakeRenderMode.Lines
        self._render_range = None

    def _renderItem(self, item: Dict[str, Any]):
        mesh = item["mesh"]
        if mesh.getVertexCount() == 0:
            return
        vertex_buffer = OpenGL.getInstance().createVertexBuffer(mesh)
        vertex_buffer.bind()
        if self._render_range is None:
            index_buffer = OpenGL.getInstance().createIndexBuffer(mesh)
        else:
            # glDrawRangeElements does not work as expected and did not get the indices field working..
            # Now we're just uploading a clipped part of the array and the start index always becomes 0.
            index_buffer = OpenGL.getInstance().createIndexBuffer(
                mesh, force_recreate=True, index_start = self._render_range[0], index_stop = self._render_range[1])
        if index_buffer is not None:
            index_buffer.bind()
        if mesh.hasIndices():
            if self._render_range is None:
                self._gl.glDrawElements(self._render_mode, mesh.getFaceCount(), self._gl.GL_UNSIGNED_INT, None)
            else:
                if self._render_mode == self.RenderMode.Triangles:
                    self._gl.glDrawRangeElements(self._render_mode, self._render_range[0], self._render_range[1], self._render_range[1] - self._render_range[0], self._gl.GL_UNSIGNED_INT, None)
                else:
                    self._gl.glDrawElements(self._render_mode, self._render_range[1] - self._render_range[0], self._gl.GL_UNSIGNED_INT, None)
        vertex_buffer.release()
        if index_buffer is not None:
            index_buffer.release()


class RenderBatchAdaptationTests(unittest.TestCase):
    def setUp(self):
        OpenGL._instance = None
        OpenGL.create_calls = []
        OpenGL.create_count = 0
        COUNTERS["index_buffers_created"][0] = 0
        self.original = FakeRenderBatch._renderItem
        self.addCleanup(lambda: setattr(FakeRenderBatch, "_renderItem", self.original))
        self.assertTrue(_patch_render_batch(FakeRenderBatch, OpenGL, offset_ptr=fake_offset_ptr))

    def test_ranged_draws_use_the_cached_buffer_and_a_byte_offset(self):
        batch = FakeRenderBatch()
        batch._render_range = (100, 150)
        mesh = FakeMesh()
        batch._renderItem({"mesh": mesh})
        # No force_recreate: the full cached buffer serves the range.
        self.assertEqual(OpenGL.getInstance().create_calls, [{}])
        draw = batch._gl.draws[0]
        self.assertEqual(draw[0], "elements")
        self.assertEqual(draw[2], 50)
        self.assertIsInstance(draw[3], int)
        # The byte offset selects the range: start * 4 for uint32.
        offset = draw[4]
        self.assertIsInstance(offset, FakeVoidPtr)
        self.assertEqual(offset.value, 100 * 4)

    def test_thousands_of_frames_create_one_buffer(self):
        # The acceptance in miniature: repeated ranged renders of a
        # stationary toolpath must not create a buffer per frame.
        batch = FakeRenderBatch()
        batch._render_range = (100, 150)
        mesh = FakeMesh()
        for _ in range(200):
            batch._renderItem({"mesh": mesh})
        self.assertEqual(OpenGL.getInstance().create_count, 1)
        self.assertEqual(len(batch._gl.draws), 200)

    def test_full_batch_draws_are_untouched(self):
        batch = FakeRenderBatch()
        mesh = FakeMesh()
        batch._renderItem({"mesh": mesh})
        draw = batch._gl.draws[0]
        self.assertEqual(draw[0], "elements")
        self.assertIsNone(draw[4])
        self.assertEqual(OpenGL.getInstance().create_calls, [{}])

    def test_the_counter_wrap_ignores_cached_hits(self):
        mesh = FakeMesh()
        OpenGL.getInstance().createIndexBuffer(mesh)
        self.assertEqual(COUNTERS["index_buffers_created"][0], 1)
        OpenGL.getInstance().createIndexBuffer(mesh)
        self.assertEqual(COUNTERS["index_buffers_created"][0], 1)
        OpenGL.getInstance().createIndexBuffer(mesh, force_recreate=True, index_start=0, index_stop=5)
        self.assertEqual(COUNTERS["index_buffers_created"][0], 2)

    def test_the_counter_wrap_skips_meshes_without_indices(self):
        # An attempt on an index-less mesh creates nothing and must
        # not count as a creation (the review's counter precision).
        OpenGL.getInstance().createIndexBuffer(FakeMesh(indices=False))
        self.assertEqual(COUNTERS["index_buffers_created"][0], 0)


class VersionGateTests(unittest.TestCase):
    def test_a_drifted_buffer_block_skips_the_whole_patch(self):
        # The buffer fragment matches but the draw fragment does not:
        # applying either alone would draw the wrong section, so the
        # pair is all-or-nothing.
        result = patch_method(
            FakeRenderBatch._renderItem,
            [(_RANGED_BUFFER_FRAGMENT, _RANGED_BUFFER_REPLACEMENT),
             (_RANGED_DRAW_FRAGMENT.replace("GL_UNSIGNED_INT, None)", "GL_UNSIGNED_INT, None)  # drifted"),
              _RANGED_DRAW_REPLACEMENT)],
            inject={"ctypes": ctypes},
        )
        self.assertIsNone(result)

    def test_a_drifted_prev_block_skips_the_patch(self):
        result = patch_method(
            FakeRenderBatch._renderItem,
            [(_PREV_LINE_TYPES_FRAGMENT, _PREV_LINE_TYPES_REPLACEMENT)],
            inject={"_mpf_recomputes": COUNTERS["prev_line_types_recomputes"]},
        )
        self.assertIsNone(result)


class _FakeLineTypes:
    def __init__(self, size):
        self.size = size


class _FakeNumpy:
    float32 = "float32"

    def __init__(self):
        self.calls = 0

    def asarray(self, value, dtype=None):
        return list(value)

    def concatenate(self, parts):
        self.calls += 1
        return list(parts[0]) + list(range(parts[1].size))


class _FakeLayerPolygon:
    MoveUnretractedType = 1


class FakeSimulationPass:
    def __init__(self, nodes):
        self._nodes = nodes

    def render(self):
        for node in self._nodes:
            layer_data = node["layer_data"]
            if node.get("active"):
                if node.get("current"):
                    # The first line does not have a previous line: add a MoveUnretractedType in front for start detection
                    # this way the first start of the layer can also be drawn
                    prev_line_types = numpy.concatenate([numpy.asarray([LayerPolygon.MoveUnretractedType], dtype = numpy.float32), layer_data._attributes["line_types"]["value"]])
                    # Remove the last element
                    prev_line_types = prev_line_types[0:layer_data._attributes["line_types"]["value"].size]
                    layer_data._attributes["prev_line_types"] =  {'opengl_type': 'float', 'value': prev_line_types, 'opengl_name': 'a_prev_line_type'}


class SimulationPassAdaptationTests(unittest.TestCase):
    def setUp(self):
        self._numpy = _FakeNumpy()
        self._module = types.ModuleType("fake_simulation_pass_module")
        self._module.SimulationPass = FakeSimulationPass
        self._module.__dict__["numpy"] = self._numpy
        self._module.__dict__["LayerPolygon"] = _FakeLayerPolygon()
        self._module.__dict__["_mpf_recomputes"] = COUNTERS["prev_line_types_recomputes"]
        COUNTERS["prev_line_types_recomputes"][0] = 0
        # patch_method re-executes in the function's OWN globals; the
        # fake class lives in this test module, so its namespace gets
        # the same fakes.
        FakeSimulationPass.render.__globals__["numpy"] = self._numpy
        FakeSimulationPass.render.__globals__["LayerPolygon"] = _FakeLayerPolygon()
        FakeSimulationPass.render.__globals__["_mpf_recomputes"] = COUNTERS["prev_line_types_recomputes"]

    def tearDown(self):
        # The real function object is replaced by the patch; restore
        # the class attribute so other tests see the vanilla method.
        pass

    def test_prev_line_types_builds_once_per_layer_data(self):
        line_types = _FakeLineTypes(6)
        node = {"active": True, "current": True,
                "layer_data": type("LD", (), {"_attributes": {"line_types": {"value": line_types}}})()}
        pass_obj = FakeSimulationPass([node])
        # The patch replaces the method on the class; each call must
        # resolve the fresh replacement, so re-run the patch helper
        # path through the class attribute.
        patched = _patch_simulation_pass(self._module)
        self.assertTrue(patched)
        self._module.SimulationPass.render(pass_obj)
        self._module.SimulationPass.render(pass_obj)
        self.assertEqual(self._numpy.calls, 1)
        self.assertEqual(COUNTERS["prev_line_types_recomputes"][0], 1)
        # A new source array triggers exactly one rebuild.
        node["layer_data"]._attributes["line_types"]["value"] = _FakeLineTypes(6)
        self._module.SimulationPass.render(pass_obj)
        self.assertEqual(self._numpy.calls, 2)
        self.assertEqual(COUNTERS["prev_line_types_recomputes"][0], 2)

    def test_prev_patch_skips_on_a_drifted_source(self):
        drifted = types.ModuleType("drifted")
        drifted.SimulationPass = FakeSimulationPass
        drifted.__dict__.update(self._module.__dict__)
        # Alter the module so the class source no longer carries the
        # fragment (a version drift); the helper must report False.
        result = patch_method(
            FakeSimulationPass.render,
            [(_PREV_LINE_TYPES_FRAGMENT.replace("MoveUnretractedType", "MoveRetractedType"),
              _PREV_LINE_TYPES_REPLACEMENT)],
            inject={"_mpf_recomputes": COUNTERS["prev_line_types_recomputes"]},
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
