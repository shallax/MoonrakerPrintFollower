"""Frozen native appearance delivery and refusal before graphics admission."""
from dataclasses import replace
import unittest

import numpy as np

from mpf.toolhead.ToolheadCaptureValues import UniformValue
from mpf.toolhead import ToolheadEnvironmentRadiance as radiance
from mpf.toolhead.ToolheadEnvironmentPaths import admit_path_inputs, freeze_path_prefix
from tests.test_toolhead_environment_paths import publication


def frozen(value):
    if type(value) is tuple: return UniformValue('list', tuple(frozen(item) for item in value))
    return UniformValue('scalar', value)


class EnvironmentRadianceTests(unittest.TestCase):
    def fixture(self):
        inputs = admit_path_inputs(*publication(), ('slice', 19))
        prefix = freeze_path_prefix(inputs, 0, 0, 6, None)
        defaults = dict(u_minimumAlbedo=(.1, .2, .3, 1.), u_starts_color=(.4, .5, .6, .7),
            u_show_travel_moves=0, u_show_helpers=1, u_show_skin=1, u_show_infill=1, u_show_starts=0,
            u_layer_view_type=1, u_extruder_opacity=((1., 1., 1., 1.),)*4)
        for index, metric in enumerate(('feedrate', 'thickness', 'line_width', 'flow_rate')):
            defaults['u_min_'+metric] = float(index)
            defaults['u_max_'+metric] = float(index+5)
        options = dict(model=UniformValue('matrix', tuple(map(float, np.eye(4).reshape(-1)))),
            uniforms=(), defaults=tuple((name, frozen(value)) for name, value in defaults.items()),
            lighting=(), light_effects=(False, False), origin=(0., 35., -15.),
            camera_light=UniformValue('vector', (1., 2., 3.)), top_element=0)
        return inputs, prefix, options

    def build(self, **overrides):
        inputs, prefix, options = self.fixture(); options.update(overrides)
        return radiance.freeze_path_radiance(inputs, prefix, **options)

    def test_actual_defaults_delivered_metrics_semantic_offsets_and_absent_lighting(self):
        result = self.build(); values = dict(result.uniforms)
        self.assertEqual(radiance._plain(values['mpf_pathMinimumAlbedo']),
                         tuple(np.float32((.1, .2, .3)).astype(float)))
        self.assertEqual(radiance._plain(values['mpf_pathMinimum']), (0., 1., 2., 3.))
        self.assertEqual(radiance._plain(values['mpf_pathMaximum']), (5., 6., 7., 8.))
        self.assertEqual(values['mpf_pathModel'].kind, 'matrix')
        self.assertEqual(values['mpf_pathVisibility'].kind, 'matrix')
        for name in ('mpf_pathAttachedCount', 'mpf_pathLightModels', 'mpf_pathCollectLight'):
            self.assertEqual(values[name].value, 0)
        inputs, _prefix, _options = self.fixture()
        for label, field in (('Colour', 'a_color'), ('MaterialColour', 'a_material_color'), ('Feedrate', 'a_feedrate')):
            offset, width = next((offset, width) for name, offset, width in inputs.fields if name == field)
            self.assertEqual(values['mpf_path'+label+'Offset'].value, offset)
            self.assertEqual(values['mpf_path'+label+'Stride'].value, width)

    def test_override_palette_theme_metrics_and_matrix_preserve_frozen_cohort_key(self):
        old = self.build()
        visibility = tuple(float(index)/16 for index in range(16))
        changes = (('u_show_helpers', frozen(0)), ('u_starts_color', frozen((.1, .9, .2, .8))),
                   ('u_extruder_opacity', UniformValue('matrix', visibility)),
                   ('u_max_line_width', frozen(20)))
        new = self.build(uniforms=changes)
        self.assertNotEqual(old.key, new.key)
        values = dict(new.uniforms)
        self.assertEqual(values['mpf_pathVisibility'], UniformValue('matrix', visibility))
        self.assertEqual(values['mpf_pathMaximum'].value[2].value, 20.)
        self.assertEqual(dict(old.uniforms)['mpf_path_showHelpers'].value, 1)
        self.assertNotEqual(old.key, self.build(origin=(1., 35., -15.)).key)
        self.assertNotEqual(old.key, self.build(camera_light=UniformValue('vector', (3., 2., 1.))).key)
        options = self.fixture()[2]
        model = list(options['model'].value); model[3] = 10.
        self.assertNotEqual(old.key, self.build(model=UniformValue('matrix', tuple(model))).key)

    @staticmethod
    def lights(count=2):
        data = dict(u_attachedCount=count, u_lightOpacity=.4)
        for index in range(count):
            data['u_attachedPosition['+str(index)+']'] = (float(index), 5., 2.)
            data['u_attachedDirection['+str(index)+']'] = (0., -1., 0.)
            data['u_attachedColour['+str(index)+']'] = (.2, .4, .8)
            data['u_attachedRange['+str(index)+']'] = 20.+index
        return tuple((name, frozen(value)) for name, value in data.items())

    def test_attached_lights_come_from_exact_capture_not_live_properties(self):
        result = self.build(lighting=self.lights(), light_effects=(True, True))
        values = dict(result.uniforms)
        self.assertEqual(values['mpf_pathLightModels'].value, 1)
        self.assertEqual(values['mpf_pathCollectLight'].value, 1)
        self.assertEqual(values['mpf_pathAttachedCount'].value, 2)
        self.assertEqual(radiance._plain(values['mpf_pathAttachedPosition[1]']), (1., 5., 2.))
        changed = list(self.lights()); changed[-1] = (changed[-1][0], frozen(25.))
        self.assertNotEqual(result.key, self.build(lighting=tuple(changed), light_effects=(True, True)).key)
        disabled = self.build(lighting=self.lights(), light_effects=(True, False))
        self.assertEqual(dict(disabled.uniforms)['mpf_pathLightModels'].value, 0)
        self.assertEqual(dict(self.build(lighting=self.lights(0), light_effects=(True, True)).uniforms)
                         ['mpf_pathLightModels'].value, 0)

    def test_historical_partial_must_certify_whole_lighting_geometry(self):
        inputs, _prefix, options = self.fixture()
        endpoint = tuple(map(float, inputs.mesh.vertices[2]))
        prefix = freeze_path_prefix(inputs, 0, 4, 6, ((0., 0., 0.), endpoint, .5))
        options.update(lighting=self.lights(), light_effects=(False, True))
        with self.assertRaisesRegex(ValueError, 'Historical partial'):
            radiance.freeze_path_radiance(inputs, prefix, **options)
        options['light_effects'] = (False, False)
        radiance.freeze_path_radiance(inputs, prefix, **options)

    def test_invalid_numeric_or_plain_shapes_never_escape_into_uniform_delivery(self):
        options = self.fixture()[2]
        cases = (dict(origin=(0., float('nan'), 0.)), dict(origin=(0., 0.)),
                 dict(origin=(0., 1e39, 0.)), dict(camera_light=frozen((1., 2., 3.))),
                 dict(model=frozen((0.,)*16)), dict(model=UniformValue('matrix', (0.,)*16)),
                 dict(light_effects=(0, 1)), dict(defaults=list(options['defaults'])),
                 dict(uniforms=(('u_show_skin', frozen(2)),)),
                 dict(uniforms=(('u_layer_view_type', frozen(.5)),)),
                 dict(uniforms=(('u_layer_view_type', frozen(1.)),)),
                 dict(uniforms=(('u_show_skin', frozen(1.00000001)),)),
                 dict(uniforms=(('u_extruder_opacity', frozen((1.,)*4)),)),
                 dict(uniforms=(('u_extruder_opacity', frozen((1.,)*16)),)),
                 dict(uniforms=(('u_starts_color', frozen((1.,)*3)),)),
                 dict(uniforms=(('u_min_feedrate', frozen(float('inf'))),)),
                 dict(uniforms=(('u_show_skin', UniformValue('unknown', 1)),)),
                 dict(uniforms=(('same', frozen(1)), ('same', frozen(2)))),
                 dict(uniforms=(('x', UniformValue('list', tuple(frozen(1) for _ in range(17)))),)),
                 dict(lighting=(('u_attachedCount', frozen(9)),)),
                 dict(lighting=self.lights()+ (('u_lightOpacity', frozen(.5)),)))
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError): self.build(**case)
        nested = frozen(1)
        for _ in range(6): nested = UniformValue('list', (nested,))
        with self.assertRaises(ValueError): self.build(uniforms=(('x', nested),))
        for value in ('fake', None, 1<<10000):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                self.build(uniforms=(('x', UniformValue('scalar', value)),))

    def test_invalid_source_prefix_and_light_fields_are_refused(self):
        inputs, prefix, options = self.fixture()
        for source, state in ((None, prefix), (inputs, None), (inputs, replace(prefix, history=10))):
            with self.assertRaises(ValueError): radiance.freeze_path_radiance(source, state, **options)
        for field, value in (('u_lightOpacity', -1.), ('u_lightOpacity', 2.), ('u_attachedRange[0]', -1.),
                             ('u_attachedPosition[0]', (1., 2.))):
            changes = dict(self.lights()); changes[field] = frozen(value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.build(lighting=tuple(changes.items()), light_effects=(True, True))
        with self.assertRaises(ValueError): self.build(top_element=3)
