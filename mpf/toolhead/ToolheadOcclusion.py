"""Share scene depth and native transparent surfaces with cropped toolheads."""
from types import SimpleNamespace

import numpy as np
from .ToolheadGLState import preserved_samples
from .ToolheadSampleTarget import ToolheadSampleTarget


class ToolheadOcclusion:
    def __init__(self):
        self._batches = ()
        self._shader = None
        self._transparent = ()
        self._transparency = None

    def prepare(self, renderer, camera, gl):
        from UM.View.RenderBatch import RenderBatch
        live = tuple(renderer.getBatches())
        if live:
            self._batches = live

        provider = renderer.getRenderPass("simulationview")
        viewport = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
        if int(gl.glGetIntegerv(0x80A9)):
            return None, None
        revision_getter = getattr(provider, "get_visible_depth_revision", None)
        revision = revision_getter(camera, viewport) if callable(revision_getter) else None
        sample_getter = getattr(provider,'get_sample_depth_revision',None)
        sample_revision = sample_getter(camera,viewport) if callable(sample_getter) else None
        items = []
        for batch in self._batches:
            if batch.renderMode != RenderBatch.RenderMode.Triangles:
                continue
            for item in batch.items:
                mesh = item["mesh"]
                vertices = mesh.getVertices()
                if vertices is None or len(vertices) < 3:
                    continue
                if batch.renderType == RenderBatch.RenderType.Solid:
                    items.append(item)
        # All visible transparent triangle surfaces participate, including
        # the unprinted part of a sliced model. The head crop's depth and
        # alpha mask keep printed paths opaque and prevent double blending
        # outside the head; object type is not a compositing rule.
        self._transparent = tuple(batch for batch in self._batches
            if batch.renderMode == RenderBatch.RenderMode.Triangles
            and batch.renderType == RenderBatch.RenderType.Transparent)
        signature = tuple((id(item["mesh"]), np.asarray(item["transformation"].getData()).tobytes()) for item in items)

        transparent_key = tuple((id(batch.shader), tuple((id(item["mesh"]),
            np.asarray(item["transformation"].getData()).tobytes(), repr(item.get("uniforms")))
            for item in batch.items)) for batch in self._transparent)

        def seed(bindings, output, original_camera, target_viewport, crop, cropped_camera=None):
            if isinstance(output,ToolheadSampleTarget):
                if (cropped_camera is None or sample_revision is None or not callable(sample_getter)
                        or sample_getter(original_camera,target_viewport) != sample_revision):return False
                with preserved_samples(bindings,output._context):
                    bindings.glEnable(0x809D)
                    for flag in (0x8E51,0x80A0,0x809E,0x809F,0x8C36):bindings.glDisable(flag)
                    if sample_revision[0] == 'paths':
                        if not provider.render_visible_depth(bindings,output,original_camera,cropped_camera,
                                target_viewport,crop,sample_revision[1]):return False
                    elif sample_revision[0] != 'empty':return False
                    self._draw_bed(bindings,original_camera,target_viewport,crop,items,cropped_camera)
                    return sample_getter(original_camera,target_viewport) == sample_revision
            if revision is not None and not provider.copy_visible_depth(bindings, output, original_camera, target_viewport, crop, revision):
                return False
            self._draw_bed(bindings, original_camera, target_viewport, crop, items, cropped_camera)
            return True
        return (revision, signature, transparent_key, sample_revision), seed

    def overlay(self, gl, output, camera):
        if not self._transparent: return True
        if self._transparency is None:
            from .ToolheadTransparency import ToolheadTransparency
            self._transparency = ToolheadTransparency()
        return self._transparency.draw(gl, output, camera, self._transparent)

    def _draw_bed(self, gl, camera, viewport, crop, items, cropped=None):
        if not items:
            return
        from UM.Math.Matrix import Matrix
        from UM.Resources import Resources
        from UM.View.GL.OpenGL import OpenGL
        from UM.View.RenderBatch import RenderBatch
        if self._shader is None:
            self._shader = OpenGL.getInstance().createShaderProgram(Resources.getPath(Resources.Shaders, "color.shader"))
        if cropped is None:
            # Standalone single-sample callers remain supported; FrameCache
            # provides its one exact proxy to every seed/head/foreground pass.
            if crop == (0,0,viewport[2],viewport[3]): cropped = camera
            else:
                left, bottom, width, height = crop
                crop_matrix = np.eye(4)
                crop_matrix[0, 0], crop_matrix[1, 1] = viewport[2] / width, viewport[3] / height
                crop_matrix[0, 3] = (viewport[2] - 2 * left) / width - 1
                crop_matrix[1, 3] = (viewport[3] - 2 * bottom) / height - 1
                projection = Matrix(crop_matrix @ camera.getProjectionMatrix().getData())
                cropped = SimpleNamespace(getProjectionMatrix=lambda: projection,
                    getInverseWorldTransformation=camera.getInverseWorldTransformation,
                    getWorldPosition=camera.getWorldPosition, getCameraLightPosition=camera.getCameraLightPosition)
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glDepthFunc(gl.GL_LESS)
        gl.glDepthMask(True)
        gl.glColorMask(False, False, False, False)
        try:
            batch = RenderBatch(self._shader, type=RenderBatch.RenderType.Solid)
            for item in items:
                batch.addItem(item["transformation"], mesh=item["mesh"])
            batch.render(cropped)
        finally:
            gl.glColorMask(True, True, True, True)
