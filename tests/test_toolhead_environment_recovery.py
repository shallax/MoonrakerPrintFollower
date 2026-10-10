"""Completed-cohort recovery admission, retirement and owned shader fallback."""
import configparser
import sys
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest

import numpy as np

from mpf.toolhead import ToolheadEnvironmentRecovery as recovery
from mpf.toolhead.ToolheadCaptureValues import UniformValue
from tests import test_toolhead_environment_radiance as radiance_support
from tools.capture_toolhead import create_context

try:
    import moderngl
except ImportError:
    moderngl = None


class EnvironmentRecoveryTests(unittest.TestCase):
    def fixture(self):
        source, prefix, options = radiance_support.EnvironmentRadianceTests().fixture()
        recipe = NS(stages=(), defaults=options['defaults'])
        frame = NS(paths=((source.mesh, source.vertex, options['model'],
                          (prefix.first*2, prefix.completed*2), None, prefix.history*2, 0),),
            recipes=((('paths', False, False), recipe),), plates=(), texture=None,
            uniforms=options['uniforms'], lighting=options['lighting'],
            light_effects=options['light_effects'], origin=options['origin'], light=options['camera_light'],
            retained_source_bytes=lambda: 1234, capture_storage_bytes=lambda: 321)
        return NS(frame=frame, generation=7, serial=19, key=('file', 'pose'), ready_fence=55)

    def test_factory_delivers_one_frozen_frame_and_complete_memory_ledger(self):
        job = self.fixture()
        paths = Mock(epoch=7, source_bytes=50, incremental_bytes=80, key=('original',))
        plates = NS(source_bytes=60, retained_bytes=90, certificate=('all original plates',))
        prior = (NS(retained_bytes=100), NS(retained_bytes=200))
        with (patch.object(recovery, '_recipe'), patch.object(recovery, 'prepare_plate_source', return_value=plates) as plate,
                patch.object(recovery, 'PathGeometry', return_value=paths) as geometry):
            owner = recovery.create_recovery('gl', 'context', job, prior, lambda: False)
        self.assertIs(owner.paths, paths); self.assertIs(owner.plates, plates)
        self.assertEqual(owner.incremental_bytes, 170)
        self.assertEqual(owner.retained_bytes, 280)
        self.assertEqual(plate.call_args.kwargs['retained_bytes'], recovery.CAPTURE_RESERVE+300+321+1234)
        self.assertEqual(geometry.call_args.kwargs['existing_bytes'], recovery.CAPTURE_RESERVE+300+321+1234+90)
        self.assertEqual(geometry.call_args.kwargs['source_ready'], 55)
        self.assertEqual(geometry.call_args.kwargs['epoch'], 7)
        self.assertEqual(geometry.call_args.kwargs['group'], 1)
        self.assertEqual(owner.key[0], job.key)
        self.assertEqual(dict(owner.radiance.uniforms)['mpf_pathProbe'].value[1].value, 35.)
        owner.close(); paths.close.assert_called_once(); self.assertIsNone(owner.plates)

    def test_singleton_budget_refusal_does_not_retry_with_a_different_topology(self):
        job = self.fixture()
        plates = NS(source_bytes=60, retained_bytes=90, certificate=('plates',))
        with (patch.object(recovery, '_recipe'), patch.object(recovery, 'prepare_plate_source', return_value=plates),
                patch.object(recovery, 'PathGeometry', side_effect=MemoryError('combined budget')) as geometry):
            self.assertIsNone(recovery.create_recovery('gl', 'context', job, (), lambda: False))
        geometry.assert_called_once()
        self.assertEqual(geometry.call_args.kwargs['group'], 1)

    def test_clean_unsupported_does_not_break_map_but_cancel_or_uncertain_ownership_propagates(self):
        job = self.fixture()
        with patch.object(recovery, '_recipe') as recipe:
            job.frame.paths = job.frame.paths*2
            self.assertIsNone(recovery.create_recovery(None, None, job, (), lambda: False))
            recipe.assert_not_called()
        job = self.fixture()
        for error in (ValueError('unsafe geometry'), MemoryError('budget'), RuntimeError('capability')):
            with patch.object(recovery, '_recipe', side_effect=error):
                self.assertIsNone(recovery.create_recovery(None, None, job, (), lambda: False))
        with patch.object(recovery, '_recipe', side_effect=RuntimeError('cancelled')):
            with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
                recovery.create_recovery(None, None, job, (), lambda: True)
        held = object()
        with patch.object(recovery, '_recipe', side_effect=recovery.GeometryUncertain(held, 'restore failed')):
            with self.assertRaises(recovery.GeometryUncertain) as caught:
                recovery.create_recovery(None, None, job, (), lambda: False)
        self.assertIs(caught.exception.owner, held)

    def test_query_and_retirement_failures_retain_composite_owner(self):
        paths = Mock(epoch=7, source_bytes=0, incremental_bytes=0, key=())
        owner = recovery.RecoveryGeometry(paths, NS(key=()), NS(retained_bytes=0, source_bytes=0, certificate=()), ())
        @contextmanager
        def broken(*args):
            yield paths
            raise recovery.GeometryUncertain(paths, 'unverified restore')
        paths.read = broken
        with self.assertRaises(recovery.GeometryUncertain) as caught:
            with owner.read('gl', 'context', 7) as admitted: self.assertIs(admitted, owner)
        self.assertIs(caught.exception.owner, owner)
        paths.close.side_effect = RuntimeError('deletion failed')
        with self.assertRaises(recovery.GeometryUncertain) as caught: owner.close()
        self.assertIs(caught.exception.owner, owner); self.assertIsNotNone(owner.plates)

    def test_shader_variant_preserves_original_map_fallback_and_legacy_bytes(self):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead', 'toolhead.shader'))
        original = parser['shaders']['fragment41core']
        assembled = recovery.recovery_fragment(original)
        start, end = original.index('vec3 environmentReflection('), original.index('float grainHash(')
        fallback = original[start:end].replace('vec3 environmentReflection(', 'vec3 mapEnvironmentReflection(', 1)
        self.assertIn(fallback, assembled)
        self.assertIn('return mapEnvironmentReflection(direction,roughness,confidence);', assembled)
        self.assertEqual(assembled.count('vec3 environmentReflection('), 1)
        self.assertIn('mpf_recovery_separated(v_position)', assembled)
        legacy = parser['shaders']['fragment']
        self.assertEqual(recovery.recovery_fragment(legacy), legacy)
        for source in ('#version 410', original.replace('float grainHash(', 'float other(')):
            with self.assertRaises(ValueError): recovery.recovery_fragment(source)

    def test_recipe_changes_refuse_even_when_the_name_matches(self):
        with self.assertRaisesRegex(ValueError, 'semantics'):
            recovery._recipe(NS(stages=(('vertex', 'void main(){}'),)))

    def test_split_receiver_preserves_original_grain_and_finish_precedence(self):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead', 'toolhead.shader'))
        original = parser['shaders']['fragment41core']
        receiver = recovery.receiver_fragment(original, single_pass=True)
        normal = original[original.index('float grainHash('):original.index('float led(')]
        self.assertIn(normal, receiver)
        rough = original[original.index('float roughness = '):original.index('float shininess = ')]
        self.assertIn(rough, receiver)
        self.assertIn('reflect(-eye, normal)', receiver)
        self.assertNotIn('if (v_color.a <= 0.0) discard;', receiver)
        self.assertIn('if (v_color.a <= 0.0) discard;', recovery.receiver_fragment(original))
        self.assertNotIn('mpf_trace_paths(', receiver)
        self.assertNotIn('float shininess = ', receiver)
        for bad in (parser['shaders']['fragment'], original.replace('out vec4 frag_color;', ''),
                    original.replace('float shininess = ', 'float changed = ')):
            with self.assertRaises(ValueError): recovery.receiver_fragment(bad)

    def test_split_lookup_contains_no_query_and_preserves_fallback_verbatim(self):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead', 'toolhead.shader'))
        original = parser['shaders']['fragment41core']
        lookup = recovery.recovery_lookup_fragment(original)
        start, end = original.index('vec3 environmentReflection('), original.index('float grainHash(')
        self.assertIn(original[start:end].replace('vec3 environmentReflection(',
            'vec3 mapEnvironmentReflection(', 1), lookup)
        self.assertIn('texelFetch(mpf_recoveredTexture,pixel,0)', lookup)
        self.assertIn('ivec2(floor(gl_FragCoord.xy))-mpf_lookupOrigin', lookup)
        self.assertIn('return mapEnvironmentReflection(direction,roughness,confidence);', lookup)
        self.assertNotIn('mpf_trace_paths(', lookup)
        self.assertNotIn('samplerBuffer', lookup)
        self.assertNotIn('texture(mpf_recoveredTexture', lookup)
        for bad in (parser['shaders']['fragment'], original.replace('float grainHash(', 'float missing(')):
            with self.assertRaises(ValueError): recovery.recovery_lookup_fragment(bad)

    def test_split_query_publishes_only_complete_lobes_without_receiver_shading(self):
        query = recovery.recovery_query_fragment()
        self.assertIn('for(int i=0;i<7;++i)', query)
        self.assertIn('if(admitted){confidence=1.;return colour;}', query)
        self.assertIn('mpf_recoveredColour=vec4(0.);', query)
        self.assertIn('confidence==1.&&mpf_path_finite(radiance)', query)
        self.assertIn('textureSize(mpf_receiverRayTexture,0)', query)
        self.assertNotIn('surfaceNormal(', query)
        self.assertNotIn('materialFinish(', query)
        self.assertNotIn('samplerCube', query)

    def test_owned_shader_loader_transforms_both_variants_and_starts_disabled(self):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead', 'toolhead.shader'))
        original = parser['shaders']['fragment41core']
        class Shader:
            def load(self, path, version):
                self.path, self.version = path, version
                self.setFragmentShader(original)
            def setFragmentShader(self, source): self.fragment = source
            def setUniformValue(self, name, value): self.uniform = name, value
        with patch.dict(sys.modules, {'UM.View.GL.ShaderProgram': NS(ShaderProgram=Shader)}):
            for opaque in (False, True):
                shader = recovery.create_recovery_shader(opaque=opaque)
                self.assertEqual(shader.version, '41core')
                self.assertEqual(shader.path, recovery.plugin_path('toolhead', 'toolhead.shader'))
                self.assertEqual(shader.uniform, ('mpf_recoveryEnabled', 0))
                self.assertIn('vec3 mapEnvironmentReflection(', shader.fragment)
                self.assertIn('return mapEnvironmentReflection(direction,roughness,confidence);', shader.fragment)
                self.assertEqual('if (v_color.a <= 0.0) discard;' in shader.fragment, not opaque)

    def test_active_shadow_and_attached_recipes_are_certified_before_allocation(self):
        for kind in ('shadow', 'light'):
            job = self.fixture()
            if kind == 'shadow':
                entry = list(job.frame.paths[0]); entry[5] = 2
                job.frame.paths = (tuple(entry),)
                job.frame.recipes += ((('paths', False, True), NS()),)
            else:
                job.frame.lighting = radiance_support.EnvironmentRadianceTests.lights()
                job.frame.light_effects = (False, True)
                job.frame.recipes += ((('light-path', False), NS()),)
            def certify(_recipe, stage='paths'):
                if stage != 'paths': raise ValueError('changed native stage')
            with (patch.object(recovery, '_recipe', side_effect=certify) as check,
                    patch.object(recovery, 'PathGeometry') as storage):
                self.assertIsNone(recovery.create_recovery(None, None, job, (), lambda: False))
                self.assertEqual(check.call_args.args[1], kind)
                storage.assert_not_called()

    def test_current_texture_and_private_capture_uploads_survive_second_stage_budget_admission(self):
        job = self.fixture(); job.frame.texture = (2, 2, bytes(16))
        job.frame.capture_storage_bytes = lambda: 337
        paths = Mock(epoch=7, source_bytes=50, incremental_bytes=80, key=())
        plates = NS(source_bytes=0, retained_bytes=90, certificate=())
        with (patch.object(recovery, '_recipe'), patch.object(recovery, 'prepare_plate_source', return_value=plates),
              patch.object(recovery, 'PathGeometry', return_value=paths) as storage):
            recovery.create_recovery(None, None, job, (), lambda: False)
        self.assertEqual(storage.call_args.kwargs['existing_bytes'], recovery.CAPTURE_RESERVE+337+1234+90)

    def test_uniform_publication_is_last_and_failed_delivery_never_enables_old_values(self):
        source, prefix, _options = radiance_support.EnvironmentRadianceTests().fixture()
        paths = NS(epoch=7, source_bytes=0, incremental_bytes=0, key=(), inputs=source, prefix=prefix,
                   prepared=NS(nodes=np.zeros((3, 8), np.float32)))
        plates = NS(retained_bytes=0, source_bytes=0, certificate=(), nodes=np.array(
            ((-1., -2., -3., 0., 1., 2., 3., -1.),), np.float32))
        owner = recovery.RecoveryGeometry(paths, NS(key=(), uniforms=(('frozen', UniformValue('scalar', 31)),)), plates, ())
        shader = Mock(); owner.apply(shader)
        self.assertEqual(shader.setUniformValue.call_args.args, ('mpf_recoveryEnabled', 1))
        deliveries = dict(call.args for call in shader.setUniformValue.call_args_list)
        self.assertEqual(deliveries['mpf_recoveryPlateLow'], [-1., -2., -3.])
        self.assertEqual(deliveries['mpf_path_sourceLineCount'], len(source.indices))
        shader = Mock()
        shader.setUniformValue.side_effect = RuntimeError('delivery failed')
        with self.assertRaises(RuntimeError): owner.apply(shader)
        self.assertNotIn(('mpf_recoveryEnabled', 1), [call.args for call in shader.setUniformValue.call_args_list])


@unittest.skipIf(moderngl is None, 'The capture OpenGL runtime is required')
class RecoveryLookupPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        source = '''#version 410
out vec4 frag_colour;
vec3 environmentReflection(vec3 direction,float roughness,out float confidence){
 confidence=.125;return vec3(.2,.3,.4);
}
float grainHash(vec3 cell){return cell.x;}
void main(){float confidence;vec3 colour=environmentReflection(vec3(0.,0.,1.),.4,confidence);
 frag_colour=vec4(colour,confidence);}
'''
        cls.program = cls.context.program(vertex_shader='''#version 410
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}
''', fragment_shader=recovery.recovery_lookup_fragment(source))
        cls.vao = cls.context.vertex_array(cls.program, [])
        cls.target = cls.context.simple_framebuffer((1, 1), components=4, dtype='f4')
        cls.texture = cls.context.texture((2, 1), 4, dtype='f4')

    @classmethod
    def tearDownClass(cls):
        cls.texture.release(); cls.target.release(); cls.vao.release(); cls.program.release(); cls.context.release()
        if cls.dll_directory is not None: cls.dll_directory.close()

    def draw(self, values=((.9, .1, .6, 1.), (.4, .8, .2, 1.)), *, enabled=1, origin=(0, 0), size=(2, 1)):
        self.texture.write(np.asarray(values, np.float32).tobytes()); self.texture.use(0)
        for name, value in dict(mpf_recoveredTexture=0, mpf_lookupEnabled=enabled,
                                mpf_lookupOrigin=origin, mpf_lookupSize=size).items():
            self.program[name].value = value
        self.target.use(); self.context.viewport = (0, 0, 1, 1)
        self.context.disable(moderngl.BLEND | moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        self.vao.render(vertices=3)
        return np.frombuffer(self.target.read(components=4, dtype='f4'), np.float32).copy()

    def test_integer_crop_lookup_preserves_colour_without_filter_or_vertical_flip(self):
        np.testing.assert_array_equal(self.draw(), np.array((.9, .1, .6, 1.), np.float32))
        np.testing.assert_array_equal(self.draw(origin=(-1, 0)), np.array((.4, .8, .2, 1.), np.float32))
        # Valid black is a real surface, not a missing-map sentinel.
        np.testing.assert_array_equal(self.draw(((0., 0., 0., 1.), (1., 1., 1., 1.))), (0., 0., 0., 1.))

    def test_disabled_stale_size_outside_and_invalid_pixels_use_original_map(self):
        fallback = np.array((.2, .3, .4, .125), np.float32)
        for options in (dict(enabled=0), dict(size=(1, 1)), dict(origin=(1, 0)), dict(origin=(-2, 0)),
                        dict(origin=(0, 1)), dict(values=((.9, .1, .6, 0.), (1., 1., 1., 1.))),
                        dict(values=((float('nan'), .1, .6, 1.), (1., 1., 1., 1.))),
                        dict(values=((.9, float('inf'), .6, 1.), (1., 1., 1., 1.)))):
            np.testing.assert_array_equal(self.draw(**options), fallback)


@unittest.skipIf(moderngl is None, 'The capture OpenGL runtime is required')
class RecoveryLayerSelectorTests(unittest.TestCase):
    """Real scalar minima distinguish surfaces that hardware depth merges."""
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        cls.guard = cls.context.simple_framebuffer((1, 1))
        vertex = '''#version 410
uniform float z;in vec2 position;out vec4 v_color;
void main(){gl_Position=vec4(position,z,1.);v_color=vec4(1.);}
'''
        cls.buffer = cls.context.buffer(np.array(((-1., -1.), (3., -1.), (-1., 3.)), np.float32).tobytes())
        cls.programs = {stage: cls.context.program(vertex_shader=vertex,
            fragment_shader=recovery.layer_selection_fragment(stage)) for stage in ('depth', 'identity')}
        cls.programs['write'] = cls.context.program(vertex_shader=vertex,
            fragment_shader='#version 410\nout vec4 colour;void main(){colour=vec4(1.);}')
        cls.vaos = {stage: cls.context.vertex_array(program, [(cls.buffer, '2f', 'position')])
                    for stage, program in cls.programs.items()}

    @classmethod
    def tearDownClass(cls):
        for vao in cls.vaos.values(): vao.release()
        for program in cls.programs.values(): program.release()
        cls.buffer.release(); cls.guard.release(); cls.context.release()
        if cls.dll_directory is not None: cls.dll_directory.close()

    def setUp(self):
        self.depth = self.context.depth_texture((1, 1))
        self.depth.compare_func = ''
        self.scalar = {stage: self.context.texture((1, 1), 1, dtype='f4') for stage in ('depth', 'identity')}
        self.targets = {stage: self.context.framebuffer((texture,), self.depth)
                        for stage, texture in self.scalar.items()}
        self.cursor = self.context.texture((1, 1), 4, dtype='f4')
        self.cursor.write(np.zeros(4, np.float32).tobytes())
        self.targets['depth'].clear(depth=.75)
        self.seed = self.depth.read()

    def tearDown(self):
        self.guard.use()
        for target in self.targets.values(): target.release()
        for texture in self.scalar.values(): texture.release()
        self.cursor.release(); self.depth.release()

    def select(self, surfaces, *, previous=None, origin=(0, 0), count=1, size=(1, 1)):
        if previous is not None: self.cursor.write(np.asarray(previous, np.float32).tobytes())
        self.cursor.use(0); self.scalar['depth'].use(1)
        for stage, sentinel in (('depth', 2.), ('identity', 16777216.)):
            program = self.programs[stage]
            for name, value in dict(mpf_previousCursor=0, mpf_selectedDepth=1,
                    mpf_layerPrevious=int(previous is not None), mpf_layerSize=size,
                    mpf_layerOrigin=origin, mpf_layerCount=count).items():
                if name in program: program[name].value = value
            self.scalar[stage].write(np.array((sentinel,), np.float32).tobytes())
            target = self.targets[stage]; target.use(); target.depth_mask = False
            self.context.viewport = (0, 0, 1, 1)
            self.context.enable(moderngl.BLEND | moderngl.DEPTH_TEST)
            self.context.disable(moderngl.CULL_FACE)
            self.context.depth_func = '<='; self.context.blend_equation = moderngl.MIN
            for identity, z in surfaces:
                program['mpf_layerBase'].value = identity; program['z'].value = z
                self.vaos[stage].render(vertices=3)
        result = tuple(float(np.frombuffer(self.scalar[stage].read(), np.float32)[0])
                       for stage in ('depth', 'identity'))
        self.assertEqual(self.depth.read(), self.seed, 'selectors wrote original visibility depth')
        self.assertEqual(self.context.error, 'GL_NO_ERROR')
        return result

    def collect(self, surfaces):
        previous = None; found = []
        for _ in range(len(surfaces)+1):
            depth, identity = self.select(surfaces, previous=previous)
            if depth == 2.:
                self.assertEqual(identity, 16777216.); return found
            self.assertGreaterEqual(depth, 0.); self.assertLessEqual(depth, 1.)
            self.assertGreaterEqual(identity, 0.)
            found.append((depth, int(identity)))
            previous = (depth, identity, 0., 1.)
        self.fail('selector failed to terminate after every original primitive')

    def test_submission_order_equal_depth_and_visibility_preserve_every_identity(self):
        surfaces = ((8, -.5), (2, -.5), (6, .1), (4, -.2), (1, .8))
        forward = self.collect(surfaces); reverse = self.collect(tuple(reversed(surfaces)))
        self.assertEqual(forward, reverse)
        self.assertEqual([identity for _, identity in forward], [2, 8, 4, 6])

    def test_distinct_raw_depths_in_one_hardware_bucket_are_both_enumerated(self):
        # Find the bucket using the actual driver storage, not a CPU D24 model.
        receipts = []
        z = np.float32(-.5)
        for _ in range(12):
            raw, _ = self.select(((0, float(z)),))
            target = self.targets['depth']; target.use(); target.depth_mask = True
            target.clear(depth=1.)
            self.context.disable(moderngl.BLEND); self.context.depth_func = '<='
            self.programs['write']['z'].value = float(z); self.vaos['write'].render(vertices=3)
            stored = self.depth.read()
            receipts.append((raw, stored, float(z)))
            target.clear(depth=.75); self.seed = self.depth.read()
            z = np.nextafter(z, np.float32(1.))
        pairs = [(a, b) for a in receipts for b in receipts if a[0] < b[0] and a[1] == b[1]]
        self.assertTrue(pairs, 'fixture must exhibit distinct raw depths in one actual bucket')
        a, b = pairs[0]
        expected = [(a[0], 7), (b[0], 2)]
        self.assertEqual(self.collect(((2, b[2]), (7, a[2]))), expected)
        self.assertEqual(self.collect(((7, a[2]), (2, b[2]))), expected)

    def test_empty_cursor_and_large_exact_identity_are_not_clamped(self):
        self.assertEqual(self.select(((16777215, -.5),)), (.25, 16777215.))
        self.assertEqual(self.select(((3, -.5),), previous=(0., 0., 0., 0.)), (2., 16777216.))

    def test_malformed_ranges_cursors_and_texture_bounds_publish_refusal(self):
        for options in (dict(count=0), dict(count=2), dict(origin=(1, 0)), dict(size=(2, 1)),
                        dict(previous=(.25, .5, 0., 1.)), dict(previous=(float('nan'), 0., 0., 1.)),
                        dict(previous=(-1., .5, 0., 0.)), dict(previous=(.25, 3., 1., 1.))):
            surfaces = ((16777215, -.5),) if options.get('count') == 2 else ((3, -.5),)
            self.assertEqual(self.select(surfaces, **options), (-1., -1.))
        with self.assertRaises(ValueError): recovery.layer_selection_fragment('combined')

    def test_original_derivatives_precede_layer_discard(self):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead', 'toolhead.shader'))
        original = parser['shaders']['fragment41core']
        recorded = recovery.layer_receiver_fragment(original)
        main = recorded[recorded.index('void main() {'):]
        self.assertLess(main.index('surfaceNormal()'), main.index('mpf_layerFault=false'))
        self.assertLess(main.index('materialFinish('), main.index('mpf_layerFault=false'))
        self.assertIn('mpf_receiverIdentity=vec4(gl_FragCoord.z,id,0.,1.)', recorded)
        self.assertNotIn('mpf_trace_paths(', recorded)

    def test_original_vertex_and_grain_payload_match_each_selected_surface(self):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead', 'toolhead.shader'))
        vertex = recovery.layer_vertex(parser['shaders']['vertex41core'])
        original = parser['shaders']['fragment41core']
        self.assertEqual(vertex.replace('invariant gl_Position;\n', ''), parser['shaders']['vertex41core'])
        for bad in ('#version 120\nvoid main() {}', parser['shaders']['vertex41core'].replace(
                'gl_Position = u_projectionMatrix * u_viewMatrix * world;', 'gl_Position = vec4(0.);')):
            with self.assertRaises(ValueError): recovery.layer_vertex(bad)
        resources = []
        try:
            def held(resource): resources.append(resource); return resource
            programmes = {stage: held(self.context.program(vertex_shader=vertex, fragment_shader=source))
                for stage, source in dict(depth=recovery.layer_selection_fragment('depth'),
                    identity=recovery.layer_selection_fragment('identity'),
                    record=recovery.layer_receiver_fragment(original),
                    reference=recovery.receiver_fragment(original)).items()}
            points = np.array(((-1., -1.), (3., -1.), (-1., 3.)), np.float32)
            values = []
            for z, roughness in ((-.5, .18), (-.2, .73)):
                for x, y in points:
                    values.append((x, y, z, 0., 0., 1., .5, .3, .1, .5, 1.,
                                   roughness, .8, 0., 1., 0., -1., -1.))
            buffer = held(self.context.buffer(np.asarray(values, np.float32).tobytes()))
            fields = (('a_vertex', 3), ('a_normal', 3), ('a_color', 4), ('a_surface', 1),
                      ('a_material', 4), ('a_body', 1), ('a_finish', 2))
            vaos = {}
            for stage, program in programmes.items():
                active = [name for name, _ in fields if name in program]
                layout = ' '.join(f'{count}f' if name in program else f'{count*4}x' for name, count in fields)
                vaos[stage] = held(self.context.vertex_array(program, [(buffer, layout, *active)]))
                for name in ('u_modelMatrix', 'u_viewMatrix', 'u_projectionMatrix',
                             'u_normalMatrix', 'u_previewRotation'):
                    if name in program: program[name].write(np.eye(4, dtype=np.float32).tobytes())
                for name, value in dict(u_environmentEnabled=1, u_lightingEnabled=1, u_orthographic=1,
                        u_viewDirection=(0., 0., 1.), u_surfaceDetail=.35, mpf_layerSize=(8, 8),
                        mpf_layerOrigin=(0, 0), mpf_layerBase=0, mpf_layerCount=2,
                        mpf_previousCursor=0, mpf_selectedDepth=1, mpf_selectedIdentity=2).items():
                    if name in program: program[name].value = value
            depth = held(self.context.depth_texture((8, 8)))
            cursor = held(self.context.texture((8, 8), 4, dtype='f4'))
            cursor.write(np.zeros((8, 8, 4), np.float32).tobytes()); cursor.use(0)
            scalars = {stage: held(self.context.texture((8, 8), 1, dtype='f4')) for stage in ('depth', 'identity')}
            scalars['depth'].use(1); scalars['identity'].use(2)
            scalar_targets = {stage: held(self.context.framebuffer((texture,), depth))
                              for stage, texture in scalars.items()}
            outputs = [held(self.context.texture((8, 8), 4, dtype='f4')) for _ in range(3)]
            target = held(self.context.framebuffer(outputs, depth))
            references = [held(self.context.texture((8, 8), 4, dtype='f4')) for _ in range(2)]
            reference = held(self.context.framebuffer(references, depth))
            target.use(); target.depth_mask = True; target.clear(depth=1.)
            seed = depth.read()
            self.context.viewport = (0, 0, 8, 8); self.context.depth_func = '<='
            self.context.disable(moderngl.CULL_FACE); self.context.enable(moderngl.DEPTH_TEST)
            for layer in range(2):
                for stage, sentinel in (('depth', 2.), ('identity', 16777216.)):
                    programmes[stage]['mpf_layerPrevious'].value = int(layer > 0)
                    scalars[stage].write(np.full((8, 8), sentinel, np.float32).tobytes())
                    scalar_targets[stage].use(); scalar_targets[stage].depth_mask = False
                    self.context.enable(moderngl.BLEND); self.context.blend_equation = moderngl.MIN
                    vaos[stage].render(vertices=6)
                self.context.disable(moderngl.BLEND)
                target.use(); target.depth_mask = False; target.clear()
                programmes['record']['mpf_layerPrevious'].value = int(layer > 0)
                vaos['record'].render(vertices=6)
                recorded = [np.frombuffer(texture.read(), np.float32).reshape(8, 8, 4).copy()
                            for texture in outputs]
                self.assertTrue(np.all(recorded[2][:, :, 3] == 1.))
                self.assertTrue(np.all(recorded[2][:, :, 1] == layer))
                reference.use(); reference.depth_mask = False; reference.clear()
                vaos['reference'].render(vertices=3, first=layer*3)
                for actual, texture in zip(recorded[:2], references, strict=True):
                    np.testing.assert_array_equal(actual, np.frombuffer(texture.read(), np.float32).reshape(8, 8, 4))
                np.testing.assert_array_equal(recorded[2][:, :, 0], np.frombuffer(
                    scalars['depth'].read(), np.float32).reshape(8, 8))
                cursor.write(recorded[2].tobytes())
                self.assertEqual(depth.read(), seed)
            # Invalid selection is a fault, never a terminal empty layer.
            programmes['record']['mpf_layerPrevious'].value = 0
            for selected_depth, identity in ((.25, .5), (-1., -1.), (2., 0.),
                                             (.25, float('nan')), (float('inf'), 0.)):
                scalars['depth'].write(np.full((8, 8), selected_depth, np.float32).tobytes())
                scalars['identity'].write(np.full((8, 8), identity, np.float32).tobytes())
                target.use(); target.depth_mask = False; target.clear()
                vaos['record'].render(vertices=6)
                fault = np.frombuffer(outputs[2].read(), np.float32).reshape(8, 8, 4)
                np.testing.assert_array_equal(fault, np.broadcast_to((-1., -1., 0., -1.), fault.shape))
                for texture in outputs[:2]:
                    self.assertTrue(np.all(np.frombuffer(texture.read(), np.float32) == 0.))
            scalars['depth'].write(np.full((8, 8), 2., np.float32).tobytes())
            scalars['identity'].write(np.full((8, 8), 16777216., np.float32).tobytes())
            target.use(); target.depth_mask = False; target.clear()
            vaos['record'].render(vertices=6)
            self.assertTrue(np.all(np.frombuffer(outputs[2].read(), np.float32) == 0.))
            programmes['record']['mpf_layerSize'].value = (7, 8)
            vaos['record'].render(vertices=6)
            self.assertTrue(np.all(np.frombuffer(outputs[2].read(), np.float32).reshape(8, 8, 4)[:, :, 3] == -1.))
            self.assertEqual(depth.read(), seed)
            self.assertEqual(self.context.error, 'GL_NO_ERROR')
        finally:
            self.guard.use()
            for resource in reversed(resources): resource.release()


if __name__ == '__main__': unittest.main()
