"""Real S1 original-shader layer sequencing, never a full-view Cura claim."""
import configparser
import ctypes
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest

import numpy as np

from mpf.toolhead import ToolheadEnvironmentLayerCapture as capture
from mpf.toolhead import ToolheadEnvironmentRecovery as recovery
from mpf.toolhead.ToolheadGLState import procedure, preserved_state
from tests import test_toolhead_environment_gl as fixture


class LayerCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.EnvironmentGLTests.setUpClass()
        cls.available = fixture.EnvironmentGLTests.available
        cls.context = fixture.EnvironmentGLTests.context
        cls.gl = getattr(fixture.EnvironmentGLTests, 'gl', None)
        cls.surface = getattr(fixture.EnvironmentGLTests, 'surface', None)

    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass()

    def setUp(self):
        if not self.available: self.skipTest('Core GL unavailable')
        self.assertTrue(self.context.makeCurrent(self.surface))
        self.owners = []; self.programs = {}; self.vaos = {}; self.buffer = 0

    def tearDown(self):
        self.context.makeCurrent(self.surface); self.gl.glFinish()
        for owner in self.owners:
            if not owner.quarantined: owner.close()
        for vao in self.vaos.values(): vao.destroy()
        if self.buffer:
            value = ctypes.c_uint(self.buffer)
            procedure(self.context, 'glDeleteBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(1, ctypes.byref(value))
        self.assertEqual(self.gl.glGetError(), 0)

    def create(self, draws=((0, 2, False),), **options):
        args = dict(key=('camera', 'pose', 'finish', 'seed'), source_key=('cohort', 7), draws=draws,
                    existing_bytes=37, byte_budget=64*1024**2); args.update(options)
        owner = capture.LayerCapture(self.gl, self.context, args.pop('width',8), args.pop('height',8), **args)
        self.owners.append(owner); return owner

    def test_context_retirement_during_admission_keeps_the_entire_capture_rooted(self):
        owner = self.create()
        targets = dict(owner.targets); retained = owner.retained_bytes
        with owner._admission():
            owner._retirement()
        self.assertTrue(owner.quarantined)
        self.assertTrue(owner.withdrawn)
        self.assertIsNone(owner.image)
        self.assertEqual(owner.targets, targets)
        self.assertEqual(owner.retained_bytes, retained)
        self.assertIn(owner, capture._uncertain)
        with self.assertRaisesRegex(RuntimeError, 'live creating context'):
            owner.step(owner.key, seed=self.seed, draw=self.draw,
                       query_scope=self.scope, query=self.query)

    def prepare(self, surfaces=((-0.5, .5), (-.2, .5)), *, samples=1):
        from PyQt6.QtGui import QMatrix4x4, QVector3D
        from PyQt6.QtOpenGL import QOpenGLShaderProgram, QOpenGLShader, QOpenGLVertexArrayObject
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead', 'toolhead.shader'))
        original = parser['shaders']['fragment41core']
        vertex = recovery.layer_vertex(parser['shaders']['vertex41core'])
        for stage, fragment in dict(depth=recovery.layer_selection_fragment('depth',samples=samples),
                identity=recovery.layer_selection_fragment('identity',samples=samples),
                record=recovery.layer_receiver_fragment(original,samples=samples)).items():
            program = QOpenGLShaderProgram()
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex), program.log())
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment), program.log())
            self.assertTrue(program.link(), program.log()); self.programs[stage] = program
            self.assertTrue(program.bind())
            for name in ('u_modelMatrix', 'u_viewMatrix', 'u_projectionMatrix', 'u_normalMatrix', 'u_previewRotation'):
                program.setUniformValue(name, QMatrix4x4())
            for name, value in dict(u_environmentEnabled=1, u_lightingEnabled=1, u_orthographic=1,
                    u_surfaceDetail=.35, u_opacity=1.).items(): program.setUniformValue(name, value)
            program.setUniformValue('u_viewDirection', QVector3D(0, 0, 1)); program.release()
        # Query has no CAD/PBR shader; it copies original recorded roughness for
        # this sequencing oracle. Geometry-query correctness is tested separately.
        query = QOpenGLShaderProgram()
        self.assertTrue(query.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, '''#version 410
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}
'''), query.log())
        self.assertTrue(query.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, '''#version 410
uniform sampler2D origin,ray;out vec4 colour;
void main(){ivec2 p=ivec2(gl_FragCoord.xy);vec4 o=texelFetch(origin,p,0),r=texelFetch(ray,p,0);
 colour=o.a==1.?vec4(r.w,o.z,r.z,1.):vec4(0.);}
'''), query.log()); self.assertTrue(query.link(), query.log()); self.programs['query'] = query
        values = []
        for index, (z, alpha) in enumerate(surfaces):
            for x, y in ((-1., -1.), (3., -1.), (-1., 3.)):
                values.append((x, y, z, 0., 0., 1., .5, .3, .1, alpha, 1.,
                               .18+index*.2, .8, 0., 1., 0., -1., -1.))
        data = np.array(values, np.float32)
        value = ctypes.c_uint()
        procedure(self.context, 'glGenBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(1, ctypes.byref(value))
        self.buffer = value.value; self.gl.glBindBuffer(0x8892, self.buffer)
        procedure(self.context, 'glBufferData', None, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint)(
            0x8892, data.nbytes, data.ctypes.data, 0x88E4)
        fields = (('a_vertex', 3), ('a_normal', 3), ('a_color', 4), ('a_surface', 1),
                  ('a_material', 4), ('a_body', 1), ('a_finish', 2))
        for stage, program in self.programs.items():
            vao = QOpenGLVertexArrayObject(); self.assertTrue(vao.create()); self.vaos[stage] = vao; vao.bind()
            self.gl.glBindBuffer(0x8892, self.buffer); offset = 0
            for name, components in fields:
                location = program.attributeLocation(name)
                if location >= 0:
                    procedure(self.context, 'glEnableVertexAttribArray', None, ctypes.c_uint)(location)
                    procedure(self.context, 'glVertexAttribPointer', None, ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
                        ctypes.c_ubyte, ctypes.c_int, ctypes.c_void_p)(location, components, 0x1406, 0, 72, ctypes.c_void_p(offset))
                offset += components*4
            vao.release()

    def seed(self, owner, depth=1.):
        owner.gl.glClearDepth(depth); owner.gl.glDepthMask(True); owner.gl.glClear(owner.gl.GL_DEPTH_BUFFER_BIT)
        return True

    @contextmanager
    def scope(self, key=('cohort', 7)):
        yield NS(key=key)

    def draw(self, stage, owner):
        program = self.programs[stage]; self.assertTrue(program.bind()); self.vaos[stage].bind()
        try:
            self.gl.glDisable(self.gl.GL_CULL_FACE)
            for index, (base, count_, _opaque) in enumerate(owner.draws):
                owner.deliver(program, index); self.gl.glDrawArrays(self.gl.GL_TRIANGLES, base*3, count_*3)
        finally: self.vaos[stage].release(); program.release()

    def query(self, source, textures):
        self.assertEqual(source.key, ('cohort', 7))
        program = self.programs['query']; self.assertTrue(program.bind()); self.vaos['query'].bind()
        try:
            for unit, (name, texture) in enumerate(zip(('origin', 'ray'), textures, strict=True)):
                self.gl.glActiveTexture(0x84C0+unit); self.gl.glBindTexture(0x0DE1, texture)
                procedure(self.context, 'glBindSampler', None, ctypes.c_uint, ctypes.c_uint)(unit, 0)
                program.setUniformValue(name, unit)
            self.gl.glDrawArrays(self.gl.GL_TRIANGLES, 0, 3)
        finally: self.vaos['query'].release(); program.release()

    def finish(self, owner, **options):
        callbacks = dict(seed=self.seed, draw=self.draw, query_scope=self.scope, query=self.query); callbacks.update(options)
        for _ in range(1024):
            self.gl.glFinish()
            if owner.step(owner.key, **callbacks): return owner.image
            self.assertIsNone(owner.image)
            if owner.withdrawn: return None
        self.fail('Small dynamic layer cycle did not complete')

    def test_guarded_original_callback_enters_with_readonly_depth_and_restores_host_mask(self):
        from mpf.toolhead.ToolheadGLState import preserved_state
        self.prepare();owner=self.create()
        self.assertFalse(owner.step(owner.key,seed=self.seed,draw=self.draw,query_scope=self.scope,query=self.query))
        self.gl.glFinish();self.gl.glDepthMask(True)
        stages=[]
        def guarded(stage,capture):
            self.assertFalse(self.gl.glGetBooleanv(self.gl.GL_DEPTH_WRITEMASK))
            stages.append(stage)
            with preserved_state(self.gl,self.context,exact_context=True):self.draw(stage,capture)
            self.assertFalse(self.gl.glGetBooleanv(self.gl.GL_DEPTH_WRITEMASK))
        self.assertFalse(owner.step(owner.key,seed=self.seed,draw=guarded,query_scope=self.scope,query=self.query))
        self.assertEqual(stages,['depth']);self.assertEqual(owner.phase,'identity')
        self.assertTrue(self.gl.glGetBooleanv(self.gl.GL_DEPTH_WRITEMASK))
        self.assertFalse(owner.quarantined)

    def original_samples(self):
        from mpf.toolhead.ToolheadSampleTarget import ToolheadSampleTarget
        target=ToolheadSampleTarget(self.gl,8,8)
        try: return target.positions
        finally: target.close()

    def test_actual_four_sample_layers_export_query_and_finish_all_planes(self):
        self.prepare(samples=4); positions=self.original_samples()
        owner=self.create(samples=4,sample_positions=positions)
        names=tuple(owner._sample_names)
        self.assertEqual(owner.graphics_bytes,64*396+16*16*52+1024*1024)
        self.assertEqual(owner.positions,positions)
        # Poison independent unit2/MS texture and fixed-function sample mask.
        self.gl.glActiveTexture(0x84C2); self.gl.glBindTexture(0x9100,names[0])
        mask=procedure(self.context,'glSampleMaski',None,ctypes.c_uint,ctypes.c_uint)
        mask(0,2); self.gl.glEnable(0x8E51); self.gl.glEnable(0x8C36)
        try:
            image=self.finish(owner)
            self.assertEqual(image.samples,4)
            np.testing.assert_array_equal(image.ranges[:,0],np.arange(256)*2)
            np.testing.assert_array_equal(image.ranges[:,1],np.full(256,2))
            np.testing.assert_array_equal(image.identities,np.tile((0,1),256))
            colours=image.colours.reshape(4,8,8,2,4)
            for plane in range(1,4): np.testing.assert_array_equal(colours[0],colours[plane])
            self.assertTrue(np.all(colours[...,0,1]==-.5))
            self.assertTrue(np.all(colours[...,1,1]==-.2))
            self.gl.glActiveTexture(0x84C2)
            self.assertEqual(self.gl.glGetIntegerv(0x9104),names[0])
            self.assertTrue(self.gl.glIsEnabled(0x8E51)); self.assertTrue(self.gl.glIsEnabled(0x8C36))
            value=ctypes.c_int()
            procedure(self.context,'glGetIntegeri_v',None,ctypes.c_uint,ctypes.c_uint,
                ctypes.POINTER(ctypes.c_int))(0x8E52,0,ctypes.byref(value))
            self.assertEqual(value.value,2)
            owner.close(); self.assertTrue(all(not self.gl.glIsTexture(name) for name in names))
        finally:
            self.gl.glDisable(0x8E51); self.gl.glDisable(0x8C36); mask(0,0xffffffff)
            self.gl.glActiveTexture(0x84C2); self.gl.glBindTexture(0x9100,0); self.gl.glActiveTexture(0x84C0)

    def test_packed_sparse_multisample_batches_match_dense_words_and_skip_empty_planes(self):
        self.prepare(samples=4);positions=self.original_samples()
        def sparse(stage,owner):
            self.gl.glEnable(self.gl.GL_SCISSOR_TEST);self.gl.glScissor(2,1,23,29)
            mask=procedure(self.context,'glSampleMaski',None,ctypes.c_uint,ctypes.c_uint)
            self.gl.glEnable(0x8E51);mask(0,10)  # only original planes 1 and 3
            try:self.draw(stage,owner)
            finally:self.gl.glDisable(0x8E51);mask(0,0xffffffff)
        packed=self.create(samples=4,sample_positions=positions,width=33,height=33)
        dense=self.create(samples=4,sample_positions=positions,width=33,height=33,packed_queries=False)
        a=self.finish(packed,draw=sparse);b=self.finish(dense,draw=sparse)
        for name in ('ranges','identities','colours'):
            np.testing.assert_array_equal(getattr(a,name).view(np.uint8),getattr(b,name).view(np.uint8))
        self.assertEqual(packed.query_records,len(a.identities))
        self.assertEqual(packed.query_batches,12)  # 667 samples, 3 batches x 2 planes x 2 layers
        self.assertLess(packed.query_batches,72)  # dense9 tiles x4 planes x2 layers
        self.assertTrue(np.all(a.ranges.reshape(4,33,33,2)[(0,2),:,:,1]==0))

    def test_four_sample_pattern_or_budget_refusal_never_substitutes_single_sample(self):
        positions=self.original_samples()
        with patch('PyQt6.QtOpenGL.QOpenGLFramebufferObject') as allocation:
            with self.assertRaises(MemoryError): self.create(samples=4,sample_positions=positions,byte_budget=1)
            with self.assertRaises(ValueError): self.create(samples=4,sample_positions=None)
            with self.assertRaises(ValueError): self.create(samples=1,sample_positions=positions)
            allocation.assert_not_called()
        with self.assertRaisesRegex(capture.GeometryUncertain,'positions') as failure:
            self.create(samples=4,sample_positions=tuple(reversed(positions)))
        owner=failure.exception.owner
        self.assertTrue(owner.quarantined); self.assertEqual(owner.positions,tuple(reversed(positions)))
        self.assertTrue(owner._sample_names); self.assertGreater(owner.retained_bytes,0)

    def test_original_transparent_layers_finish_only_on_empty_selector_and_keep_exact_radiance(self):
        self.prepare(); owner = self.create(); image = self.finish(owner)
        np.testing.assert_array_equal(image.ranges[:, 0], np.arange(64)*2)
        np.testing.assert_array_equal(image.ranges[:, 1], np.full(64, 2))
        np.testing.assert_array_equal(image.identities, np.tile((0, 1), 64))
        colours = image.colours.reshape(8, 8, 2, 4)
        self.assertTrue(np.all(colours[:, :, 0, 1] == -.5)); self.assertTrue(np.all(colours[:, :, 1, 1] == -.2))
        self.assertEqual(owner.layer, 2); self.assertGreater(owner.retained_bytes, owner.graphics_bytes)
        # Static visibility is not written by either selector or receiver.
        with preserved_state(self.gl, self.context):
            owner.targets['records'].bind(); depth = np.empty((8, 8), np.float32)
            procedure(self.context, 'glReadPixels', None, *([ctypes.c_int]*4), ctypes.c_uint,
                ctypes.c_uint, ctypes.c_void_p)(0, 0, 8, 8, 0x1902, 0x1406, depth.ctypes.data)
            np.testing.assert_array_equal(depth, np.ones((8, 8), np.float32))
        owner.close(); self.assertEqual(owner.retained_bytes, 0)

    def test_original_opaque_lequal_and_translucent_less_do_not_admit_hidden_codepth_glass(self):
        self.prepare(((-.5, 1.), (-.7, .5), (-.5, .5)))
        owner = self.create(draws=((0, 1, True), (1, 2, False)))
        image = self.finish(owner, seed=lambda target: self.seed(target, .25))
        np.testing.assert_array_equal(image.identities, np.tile((0, 1), 64))
        self.assertTrue(np.all(image.ranges[:, 1] == 2)); self.assertEqual(owner.layer, 2)

    def test_fallback_radiance_still_enumerates_back_layers_and_missing_records_refuse_whole_result(self):
        self.prepare()
        def fallback(_source, _textures): self.gl.glClearColor(0, 0, 0, 0); self.gl.glClear(self.gl.GL_COLOR_BUFFER_BIT)
        image = self.finish(self.create(), query=fallback)
        self.assertEqual(len(image.identities), 128); self.assertTrue(np.all(image.colours == 0.))
        owner = self.create()
        def missing(stage, target):
            self.draw(stage, target)
            if stage == 'record': target._clear('records')
        with self.assertRaises(capture.GeometryUncertain): self.finish(owner, draw=missing)
        self.assertIsNone(owner.image); self.assertTrue(owner.quarantined)

    def test_source_ticket_loss_stale_aba_and_failed_seed_never_publish_partial_layers(self):
        self.prepare(); owner = self.create()
        self.assertIsNone(self.finish(owner, query_scope=lambda: self.scope(('new-cohort', 8))))
        self.assertTrue(owner.withdrawn)
        self.assertFalse(owner.step(owner.key, seed=self.seed, draw=self.draw, query_scope=self.scope, query=self.query))
        owner = self.create()
        self.assertFalse(owner.step(('other',), seed=self.seed, draw=self.draw, query_scope=self.scope, query=self.query))
        self.assertFalse(owner.step(owner.key, seed=self.seed, draw=self.draw, query_scope=self.scope, query=self.query))
        owner = self.create(); self.assertIsNone(self.finish(owner, seed=lambda target: False))

    def test_readback_neutralizes_pack_pbo_stride_and_copies_bounded_original_rows(self):
        self.prepare(); owner = self.create(); gl = self.gl
        name = ctypes.c_uint()
        procedure(self.context, 'glGenBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(1, ctypes.byref(name))
        original = capture.procedure; sizes = []
        clamp = procedure(self.context, 'glClampColor', None, ctypes.c_uint, ctypes.c_uint)
        previous_clamp = int(gl.glGetIntegerv(0x891C))
        def resolve(context, function, *types):
            call = original(context, function, *types)
            if function != 'glReadPixels': return call
            def read(x, y, width, height, format_, type_, destination):
                sizes.append(width*height*(4 if format_ == 0x1903 else 16)); return call(x, y, width, height, format_, type_, destination)
            return read
        try:
            gl.glBindBuffer(0x88EB, name.value)
            clamp(0x891C, 1)
            gl.glPixelStorei(0x0D02, 23); gl.glPixelStorei(0x0D03, 4); gl.glPixelStorei(0x0D04, 5)
            with patch.object(capture, 'READ_BYTES', 128), patch.object(capture, 'procedure', side_effect=resolve):
                image = self.finish(owner)
            self.assertEqual(len(image.identities), 128); self.assertTrue(all(size <= 128 for size in sizes))
            self.assertEqual(gl.glGetIntegerv(0x88ED), name.value)
            self.assertEqual(gl.glGetIntegerv(0x891C), 1)
            for parameter, expected in ((0x0D02, 23), (0x0D03, 4), (0x0D04, 5)): self.assertEqual(gl.glGetIntegerv(parameter), expected)
        finally:
            gl.glBindBuffer(0x88EB, 0)
            clamp(0x891C, previous_clamp)
            for key in (0x0D02, 0x0D03, 0x0D04): gl.glPixelStorei(key, 0)
            procedure(self.context, 'glDeleteBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(1, ctypes.byref(name))

    def test_admitted_callbacks_cannot_retire_resources_or_submit_a_nested_generation(self):
        self.prepare(); owner = self.create()
        def nested(target):
            self.assertFalse(target.step(('replacement',), seed=self.seed, draw=self.draw, query_scope=self.scope, query=self.query))
            self.assertFalse(target.withdrawn); return self.seed(target)
        self.assertIsNotNone(self.finish(owner, seed=nested))
        owner = self.create()
        def close(target): target.close(); self.fail('Retired target reused')
        with self.assertRaises(capture.GeometryUncertain): self.finish(owner, seed=close)
        self.assertFalse(owner.closed); self.assertTrue(owner.quarantined); self.assertEqual(len(owner.targets), 6)

    def test_ledger_draw_range_and_dimension_refusal_precede_target_creation(self):
        with patch('PyQt6.QtOpenGL.QOpenGLFramebufferObject') as allocation:
            with self.assertRaises(MemoryError): self.create(byte_budget=1)
            for draws in ((), ((0, 1, True), (0, 2, False)), ((16777216, 1, True),), ((0, 1, 1),)):
                with self.assertRaises(ValueError): self.create(draws=draws)
            with self.assertRaises(ValueError): self.create(existing_bytes=True)
            allocation.assert_not_called()

    def test_partial_teardown_failure_keeps_an_inspectable_conservative_receipt(self):
        owner = self.create(); receipt = owner.retained_bytes
        self.context.aboutToBeDestroyed.disconnect(owner._retirement)
        with self.assertRaises(capture.GeometryUncertain): owner.close()
        self.assertTrue(owner.quarantined); self.assertFalse(owner.closed)
        self.assertIsNotNone(owner.builder); self.assertEqual(owner.retained_bytes, receipt)

    def test_read_clamp_preserves_negative_fault_and_independent_pack_restoration(self):
        self.prepare(); gl = self.gl
        clamp = procedure(self.context, 'glClampColor', None, ctypes.c_uint, ctypes.c_uint)
        previous = int(gl.glGetIntegerv(0x891C)); clamp(0x891C, 1)
        try:
            owner = self.create()
            def fault(stage, target):
                self.draw(stage, target)
                if stage == 'depth': target._clear('depth', -1.)
            with self.assertRaisesRegex(capture.GeometryUncertain, 'Malformed'): self.finish(owner, draw=fault)
            self.assertEqual(gl.glGetIntegerv(0x891C), 1)
            owner = self.create()
            while owner.phase != 'selectors':
                self.gl.glFinish(); owner.step(owner.key, seed=self.seed, draw=self.draw, query_scope=self.scope, query=self.query)
            original = gl.glPixelStorei; restored = []
            def store(key, value):
                if key == 0x0D00 and value == 0:
                    restored.append(key)
                    if len(restored) == 2: raise RuntimeError('First pack restore failed')
                return original(key, value)
            with patch.object(gl, 'glPixelStorei', side_effect=store):
                with self.assertRaisesRegex(capture.GeometryUncertain, 'pixel-pack'): self.finish(owner)
            self.assertEqual(gl.glGetIntegerv(0x891C), 1); self.assertEqual(gl.glGetIntegerv(0x88ED), 0)
        finally: clamp(0x891C, previous)


if __name__ == '__main__': unittest.main()
