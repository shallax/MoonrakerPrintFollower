"""Blend native transparent surfaces in front of a cropped toolhead image."""


class ToolheadTransparency:
    def __init__(self):
        self._front = self._merged = self._shader = self._vao = None
        self._size = None

    def draw(self, gl, head, camera, batches):
        if not batches:
            return True
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLVertexArrayObject
        from UM.View.GL.ShaderProgram import ShaderProgram
        from UM.View.RenderBatch import RenderBatch
        width, height = head.size().width(), head.size().height()
        if head.format().samples():
            return False
        try:
            if self._size != (width, height):
                self._front = QOpenGLFramebufferObject(width, height, head.format())
                self._merged = QOpenGLFramebufferObject(width, height)
                if not self._front.isValid() or not self._merged.isValid():
                    raise RuntimeError("Transparent toolhead composition unavailable")
                self._size = width, height
            if self._shader is None:
                shader = ShaderProgram()
                if not shader.setVertexShader("#version 410\nvoid main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }"):
                    raise RuntimeError("Transparent composition vertex shader unavailable")
                if not shader.setFragmentShader("""#version 410
uniform sampler2D u_head;
uniform sampler2D u_front;
out vec4 frag_color;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec4 head = texelFetch(u_head, p, 0);
    vec4 front = texelFetch(u_front, p, 0);
    // Keep head coverage. The native scene already contains these surfaces
    // outside it, and beneath any transparent part of the head.
    frag_color = vec4(head.rgb * (1.0 - front.a) + front.rgb * head.a, head.a);
}
"""):
                    raise RuntimeError("Transparent composition fragment shader unavailable")
                shader.build()
                shader.setUniformValue("u_head", 0)
                shader.setUniformValue("u_front", 1)
                self._shader = shader
            if self._vao is None:
                self._vao = QOpenGLVertexArrayObject()
                if not self._vao.create():
                    self._vao = None
                    raise RuntimeError("Transparent composition vertex array unavailable")
            self._front.bind()
            gl.glViewport(0, 0, width, height)
            gl.glDisable(0x0C11)
            gl.glColorMask(True, True, True, True)
            gl.glClearColor(0, 0, 0, 0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            QOpenGLFramebufferObject.blitFramebuffer(self._front, head, gl.GL_DEPTH_BUFFER_BIT, 0x2600)
            if gl.glGetError():
                return False
            self._front.bind()
            for source in batches:
                batch = RenderBatch(source.shader, type=RenderBatch.RenderType.Transparent,
                    mode=source.renderMode, backface_cull=source.backfaceCull, range=source.renderRange,
                    state_setup_callback=lambda bindings: (bindings.glDepthFunc(bindings.GL_LESS),
                        bindings.glBlendFuncSeparate(bindings.GL_SRC_ALPHA, bindings.GL_ONE_MINUS_SRC_ALPHA,
                            bindings.GL_ONE, bindings.GL_ONE_MINUS_SRC_ALPHA)))
                for item in source.items:
                    batch.addItem(item["transformation"], mesh=item["mesh"], uniforms=item.get("uniforms"),
                        normal_transformation=item.get("normal_transformation"))
                batch.render(camera)
            self._merged.bind()
            gl.glDisable(gl.GL_DEPTH_TEST)
            gl.glDisable(gl.GL_BLEND)
            gl.glDisable(gl.GL_CULL_FACE)
            self._shader.bind()
            try:
                self._vao.bind()
                for unit, texture in enumerate((head.texture(), self._front.texture())):
                    gl.glActiveTexture(gl.GL_TEXTURE0 + unit)
                    gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
                gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
            finally:
                self._vao.release()
                self._shader.release()
                for unit in (1, 0):
                    gl.glActiveTexture(gl.GL_TEXTURE0 + unit)
                    gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
            QOpenGLFramebufferObject.blitFramebuffer(head, self._merged, gl.GL_COLOR_BUFFER_BIT, 0x2600)
            return not gl.glGetError()
        finally:
            head.bind()
            gl.glViewport(0, 0, width, height)
            gl.glColorMask(True, True, True, True)
            gl.glDepthMask(True)
            gl.glDepthFunc(gl.GL_LESS)
