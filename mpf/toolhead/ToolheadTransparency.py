"""Blend native transparent surfaces in front of a cropped toolhead image."""


import ctypes
from collections import OrderedDict
from weakref import ref
from .ToolheadGLState import preserved_state, preserved_samples, procedure
from .ToolheadSampleTarget import ToolheadSampleTarget, sample_blit


class ToolheadTransparency:
    def __init__(self):
        self._front = self._merged = self._shader = self._vao = None
        self._size = self._ordinary_key = None
        self._pool = OrderedDict()
        self._pool_bytes = self._pool_hits = 0
        self._renderer_gl = None
        self._software_scissor = False
        self._sample_context = self._sample_shader = self._sample_vao = None
        self._sample_front = self._sample_merged = self._sample_size = None
        self._sample_generation = self._sample_retirement = None

    @staticmethod
    def _target_key(size, fmt):
        attachment = fmt.attachment()
        return (*size, fmt.samples(), getattr(attachment, "value", attachment),
            fmt.internalTextureFormat(), fmt.mipmap())

    def _stash_front(self):
        key, front = self._ordinary_key, self._front
        if (key is not None and front is not None and front.isValid()
                and key[2] == 0 and key[4] in (0x8058, 0x1908) and not key[5]):
            # Only ordinary RGBA8, non-mipmapped targets enter this pool.
            # Twelve bytes conservatively includes packed depth/stencil storage.
            charge = key[0]*key[1]*12
            if charge <= 24 << 20:
                old = self._pool.pop(key, None)
                if old is not None: self._pool_bytes -= old[1]
                self._pool[key] = front, charge
                self._pool_bytes += charge
                while len(self._pool) > 4 or self._pool_bytes > 24 << 20:
                    _, (_, removed) = self._pool.popitem(last=False)
                    self._pool_bytes -= removed
        self._front = self._merged = self._size = self._ordinary_key = None

    def _take_front(self, key):
        entry = self._pool.pop(key, None)
        if entry is None: return None
        self._pool_bytes -= entry[1]
        if not entry[0].isValid(): return None
        self._pool_hits += 1
        return entry[0]

    def _clear_ordinary_targets(self):
        self._front = self._merged = self._size = self._ordinary_key = None
        self._pool.clear()
        self._pool_bytes = 0

    def release_targets(self):
        self._retire_sample_targets()
        self._clear_ordinary_targets()
        self._renderer_gl = None

    def retire_for_ordinary(self, width, height):
        """Retain bounded exact-format foreground storage across crop changes."""
        self._retire_sample_targets()
        if self._size != (width, height): self._stash_front()

    def draw(self, gl, head, camera, batches, *, clip=None):
        if not batches:
            return True
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLVertexArrayObject
        from UM.View.GL.ShaderProgram import ShaderProgram
        from UM.View.RenderBatch import RenderBatch
        width, height = head.size().width(), head.size().height()
        if head.format().samples():
            if clip is not None: return False
            return self._draw_sampled(gl,head,camera,batches)
        if self._renderer_gl is not gl:
            renderer = gl.glGetString(0x1F01)
            self._software_scissor = renderer in ("Apple Software Renderer", b"Apple Software Renderer")
            self._renderer_gl = gl
        software_clip = clip is not None and self._software_scissor
        scissor = None
        def constrain(bindings):
            if software_clip:
                bindings.glDisable(0x0C11)
            elif clip is not None:
                bindings.glEnable(0x0C11)
                bindings.glScissor(*clip)
        try:
            if clip is not None:
                if (len(clip) != 4 or any(type(v) is not int for v in clip)
                        or clip[0] < 0 or clip[1] < 0 or clip[2] <= 0 or clip[3] <= 0
                        or clip[0]+clip[2] > width or clip[1]+clip[3] > height):
                    raise ValueError('Foreground clip must fit its exact target')
                scissor = bool(gl.glIsEnabled(0x0C11)), tuple(map(int, gl.glGetIntegerv(0x0C10)))
            self._retire_sample_targets()
            key = self._target_key((width, height), head.format())
            if self._ordinary_key != key:
                self._stash_front()
                front = self._take_front(key)
                if front is None: front = QOpenGLFramebufferObject(width, height, head.format())
                if not front.isValid():
                    raise RuntimeError("Transparent toolhead composition unavailable")
                self._front = front
                self._ordinary_key = self._target_key((width, height), front.format())
                self._size = width, height
            if self._shader is None:
                shader = ShaderProgram()
                if not shader.setVertexShader("#version 410\nvoid main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }"):
                    raise RuntimeError("Transparent composition vertex shader unavailable")
                if not shader.setFragmentShader("""#version 410
uniform sampler2D u_front;
uniform int u_clipEnabled;
uniform vec4 u_clipRect;
out vec4 frag_color;
void main() {
    if (u_clipEnabled != 0 && (gl_FragCoord.x < u_clipRect.x || gl_FragCoord.y < u_clipRect.y
            || gl_FragCoord.x >= u_clipRect.z || gl_FragCoord.y >= u_clipRect.w)) discard;
    ivec2 p = ivec2(gl_FragCoord.xy);
    // Fixed-function blending reads the destination without texture feedback.
    frag_color = texelFetch(u_front, p, 0);
}
"""):
                    raise RuntimeError("Transparent composition fragment shader unavailable")
                shader.build()
                shader.setUniformValue("u_front", 1)
                self._shader = shader
            if self._vao is None:
                self._vao = QOpenGLVertexArrayObject()
                if not self._vao.create():
                    self._vao = None
                    raise RuntimeError("Transparent composition vertex array unavailable")
            self._front.bind()
            gl.glViewport(0, 0, width, height)
            if clip is None: gl.glDisable(0x0C11)
            else: constrain(gl)
            gl.glColorMask(True, True, True, True)
            gl.glClearColor(0, 0, 0, 0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            if clip is None or software_clip:
                QOpenGLFramebufferObject.blitFramebuffer(self._front, head, gl.GL_DEPTH_BUFFER_BIT, 0x2600)
            else:
                from PyQt6.QtCore import QRect
                # Explicit matching rectangles avoid relying on inherited
                # scissor state during Qt's framebuffer binding operations.
                rect = QRect(*clip)
                gl.glDisable(0x0C11)
                QOpenGLFramebufferObject.blitFramebuffer(self._front, rect, head, rect, gl.GL_DEPTH_BUFFER_BIT, 0x2600)
            if gl.glGetError():
                return False
            self._front.bind()
            constrain(gl)
            for source in batches:
                batch = RenderBatch(source.shader, type=RenderBatch.RenderType.Transparent,
                    mode=source.renderMode, backface_cull=source.backfaceCull, range=source.renderRange,
                    state_setup_callback=lambda bindings: (constrain(bindings), bindings.glDepthFunc(bindings.GL_LESS),
                        bindings.glBlendFuncSeparate(bindings.GL_SRC_ALPHA, bindings.GL_ONE_MINUS_SRC_ALPHA,
                            bindings.GL_ONE, bindings.GL_ONE_MINUS_SRC_ALPHA)))
                for item in source.items:
                    batch.addItem(item["transformation"], mesh=item["mesh"], uniforms=item.get("uniforms"),
                        normal_transformation=item.get("normal_transformation"))
                batch.render(camera)
            if not head.bind():
                raise RuntimeError("Transparent composition destination could not bind")
            constrain(gl)
            gl.glDisable(gl.GL_DEPTH_TEST)
            gl.glDisable(gl.GL_CULL_FACE)
            # Preserve coverage while adding premultiplied foreground colour:
            # front.rgb * head.a + head.rgb * (1-front.a), alpha = head.a.
            blend = tuple(gl.glGetIntegerv(key) for key in (0x80C9, 0x80C8, 0x80CB, 0x80CA))
            equations = tuple(gl.glGetIntegerv(key) for key in (0x8009, 0x883D))
            try:
                gl.glEnable(gl.GL_BLEND)
                gl.glBlendEquationSeparate(0x8006, 0x8006)
                gl.glBlendFuncSeparate(0x0304, 0x0303, 0, 1)
                # Apple's software scissor can omit whole small rectangles.
                # Render the native foreground fully, then clip only composition
                # in fragment coordinates; destination exterior stays untouched.
                self._shader.setUniformValue("u_clipEnabled", int(software_clip))
                if software_clip:
                    x, y, w, h = clip
                    self._shader.setUniformValue("u_clipRect", [float(x), float(y), float(x+w), float(y+h)])
                self._shader.bind()
                self._vao.bind()
                gl.glActiveTexture(gl.GL_TEXTURE0 + 1)
                gl.glBindTexture(gl.GL_TEXTURE_2D, self._front.texture())
                gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
            finally:
                try:
                    self._vao.release()
                    self._shader.release()
                    gl.glActiveTexture(gl.GL_TEXTURE0 + 1)
                    gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    gl.glActiveTexture(gl.GL_TEXTURE0)
                finally:
                    try:
                        try: gl.glBlendFuncSeparate(*blend)
                        finally: gl.glBlendEquationSeparate(*equations)
                    finally: gl.glDisable(gl.GL_BLEND)
            return not gl.glGetError()
        finally:
            try:
                head.bind()
                gl.glViewport(0, 0, width, height)
                gl.glColorMask(True, True, True, True)
                gl.glDepthMask(True)
                gl.glDepthFunc(gl.GL_LESS)
            finally:
                if scissor is not None:
                    try: gl.glScissor(*scissor[1])
                    finally: (gl.glEnable if scissor[0] else gl.glDisable)(0x0C11)

    def _draw_sampled(self, gl, head, camera, batches):
        """Foreground blend at each original depth sample BEFORE colour resolve."""
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLVertexArrayObject
        from UM.View.GL.ShaderProgram import ShaderProgram
        from UM.View.RenderBatch import RenderBatch
        context=QOpenGLContext.currentContext()
        if (not isinstance(head,ToolheadSampleTarget) or not head.isValid()
                or context is None or head._context is not context):return False
        if (tuple(map(int,gl.glGetIntegerv(0x0B40))) != (0x1B02,0x1B02)
                or any(gl.glIsEnabled(flag) for flag in (0x8C89,0x0B90,0x0BF2,*range(0x3000,0x3008)))):
            return False
        width,height=head.width(),head.height()
        if width*height*72 > 128*1024*1024:
            raise RuntimeError('Sample foreground exceeds its combined graphics memory budget')
        size=(width,height,4,head.positions)
        try:
            with preserved_state(gl,context),preserved_samples(gl,context):
                self._clear_ordinary_targets()
                if self._sample_context is not context:
                    self._sample_front=self._sample_merged=self._sample_shader=self._sample_vao=None
                    self._sample_size=None
                    self._sample_context=context
                    self._sample_generation=object();token=self._sample_generation;owner=ref(self)
                    def retired():
                        instance=owner()
                        if instance is not None and instance._sample_generation is token:
                            instance._sample_context=instance._sample_size=instance._sample_shader=instance._sample_vao=None
                            instance._sample_front=instance._sample_merged=instance._sample_generation=None
                    self._sample_retirement=retired
                    from PyQt6.QtCore import Qt
                    context.aboutToBeDestroyed.connect(retired,Qt.ConnectionType.DirectConnection)
                if (self._sample_size != size or self._sample_front is None or self._sample_merged is None
                        or not self._sample_front.isValid() or not self._sample_merged.isValid()):
                    self._retire_sample_targets()
                    self._sample_front=ToolheadSampleTarget(gl,width,height)
                    self._sample_merged=ToolheadSampleTarget(gl,width,height)
                    if any(owner.positions != head.positions for owner in (self._sample_front,self._sample_merged)):
                        raise RuntimeError('Foreground sample pattern differs from the head')
                    self._sample_size=size
                if self._sample_shader is None:
                    shader=ShaderProgram()
                    if not shader.setVertexShader('#version 410\nvoid main(){vec2 p=vec2((gl_VertexID << 1) & 2,gl_VertexID & 2);gl_Position=vec4(p*2.0-1.0,0,1);}'):
                        raise RuntimeError('Sample foreground vertex shader unavailable')
                    if not shader.setFragmentShader("""#version 410
uniform sampler2DMS u_head;
uniform sampler2DMS u_front;
uniform int u_sample;
out vec4 frag_color;
void main(){
    ivec2 p=ivec2(gl_FragCoord.xy);
    vec4 head=texelFetch(u_head,p,u_sample);
    vec4 front=texelFetch(u_front,p,u_sample);
    frag_color=vec4(head.rgb*(1.0-front.a)+front.rgb*head.a,head.a);
}
"""):
                        raise RuntimeError('Sample foreground fragment shader unavailable')
                    shader.build();shader.setUniformValue('u_head',0);shader.setUniformValue('u_front',1)
                    self._sample_shader=shader
                if self._sample_vao is None:
                    vao=QOpenGLVertexArrayObject()
                    if not vao.create():raise RuntimeError('Sample foreground vertex array unavailable')
                    self._sample_vao=vao
                gl.glEnable(0x809D)
                for flag in (0x8E51,0x80A0,0x809E,0x809F,0x8C36):gl.glDisable(flag)
                front,merged=self._sample_front,self._sample_merged
                if not front.bind():raise RuntimeError('Sample foreground target unavailable')
                gl.glViewport(0,0,width,height);gl.glDisable(0x0C11)
                gl.glColorMask(True,True,True,True);gl.glClearColor(0,0,0,0)
                gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                sample_blit(gl,front,head,buffers=gl.GL_DEPTH_BUFFER_BIT)
                if not front.bind():raise RuntimeError('Sample foreground target unavailable')
                for source in batches:
                    batch=RenderBatch(source.shader,type=RenderBatch.RenderType.Transparent,
                        mode=source.renderMode,backface_cull=source.backfaceCull,range=source.renderRange,
                        state_setup_callback=lambda bindings:(bindings.glDepthFunc(bindings.GL_LESS),
                            bindings.glBlendEquationSeparate(0x8006,0x8006),
                            bindings.glBlendFuncSeparate(bindings.GL_SRC_ALPHA,bindings.GL_ONE_MINUS_SRC_ALPHA,
                                bindings.GL_ONE,bindings.GL_ONE_MINUS_SRC_ALPHA)))
                    for item in source.items:
                        batch.addItem(item['transformation'],mesh=item['mesh'],uniforms=item.get('uniforms'),
                                      normal_transformation=item.get('normal_transformation'))
                    batch.render(camera)
                if not merged.bind():raise RuntimeError('Sample merge target unavailable')
                gl.glViewport(0,0,width,height);gl.glDisable(0x0C11)
                gl.glDisable(gl.GL_DEPTH_TEST);gl.glDepthMask(False)
                gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE)
                gl.glColorMask(True,True,True,True)
                shader=self._sample_shader;vao=self._sample_vao
                mask=procedure(context,'glSampleMaski',None,ctypes.c_uint,ctypes.c_uint)
                try:
                    if shader.bind() is False:raise RuntimeError('Sample foreground shader binding failed')
                    if vao.bind() is False:raise RuntimeError('Sample foreground array binding failed')
                    for unit,texture in enumerate((head.texture(),front.texture())):
                        gl.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x9100,texture)
                    gl.glEnable(0x8E51)
                    for sample in range(4):
                        shader.setUniformValue('u_sample',sample)
                        mask(0,1<<sample)
                        gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
                    if gl.glGetError():raise RuntimeError('Sample foreground merge failed')
                finally:
                    first=None
                    for action in (vao.release,shader.release):
                        try:action()
                        except Exception as error:
                            if first is None:first=error
                    if first is not None:raise first
                sample_blit(gl,head,merged,buffers=gl.GL_COLOR_BUFFER_BIT)
                return True
        finally:
            head.bind();gl.glViewport(0,0,width,height)
            gl.glColorMask(True,True,True,True);gl.glDepthMask(True);gl.glDepthFunc(gl.GL_LESS)

    def _retire_sample_targets(self):
        # Drop BOTH obsolete owners before the next allocation. Keep names
        # leased on a failed retirement; never overwrite an unretired target.
        for name in ('_sample_front', '_sample_merged'):
            target = getattr(self, name)
            if target is not None:
                target.close()
                setattr(self, name, None)
        self._sample_size = None
