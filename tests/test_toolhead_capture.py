"""The showcase keeps the chosen CAD palette, lights and physical dimensions."""
import hashlib
import json
import struct
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tools import capture_toolhead as capture


class ToolheadCaptureTests(unittest.TestCase):
    def test_analytic_coverage_is_software_only_and_retains_original_depth_and_attributes(self):
        source = capture.configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        source.read(capture.ROOT / 'mpf/toolhead/scene-lighting.shader')
        vertex = source['shaders']['vertex41core']
        fragment = source['shaders']['fragment41core']
        for renderer in ('Apple M1 Pro', 'llvmpipe', 'AMD Radeon', 'NVIDIA'):
            self.assertIsNone(capture.capture_receiver_geometry(renderer))
            self.assertIs(capture.capture_receiver_coverage(fragment, renderer), fragment)
        adapted = capture.capture_receiver_coverage(fragment, 'Apple Software Renderer')
        self.assertEqual(adapted.count('void main() {'), 1)
        self.assertIn('gl_FragDepth=float(originalDepth)', adapted)
        self.assertIn('edges[i]<0.0', adapted)
        self.assertIn('captureInclusive[i]==0', adapted)
        # The actual production light function remains identical.
        start = fragment.index('vec3 lightSurface(')
        stop = fragment.index('void main() {')
        self.assertIn(fragment[start:stop], adapted)
        transformed = capture.capture_receiver_vertex(vertex, 'Apple Software Renderer')
        self.assertIn('out vec3 g_vertex;', transformed)
        self.assertIn('gl_Position = u_projectionMatrix * u_viewMatrix * world;', transformed)
        geometry = capture.capture_receiver_geometry('Apple Software Renderer')
        self.assertIn('low=floor(min(p[0],min(p[1],p[2])))-1.0', geometry)
        self.assertIn('max_vertices=3', geometry)
        self.assertIn('2.0*extent.x', geometry)
        self.assertIn('2.0*extent.y', geometry)
        emission = geometry[geometry.index('for(int i=0;i<3;++i) {\n        // EmitVertex'):]
        for output in ('captureArea=', 'captureInclusive=', 'captureEdges[k]=',
                       'captureVertices[k]=', 'captureNormals[k]=', 'captureColours[k]=', 'captureDepths[k]='):
            self.assertIn(output, emission[:emission.index('EmitVertex();')])
        for changed in (fragment.replace('in vec3 f_vertex;', ''), fragment.replace('void main() {', 'void different() {')):
            with self.assertRaises(RuntimeError):
                capture.capture_receiver_coverage(changed, 'Apple Software Renderer')

    def test_opposite_canonical_edges_have_complementary_ownership_without_a_crack(self):
        # This is deliberately near a float32 raster edge at large supersample
        # coordinates, the failure class observed at actual CI pixel(867,807).
        # Actual projected shared endpoints of fixture facets16314/44780.
        a = np.array((1790.3144240379333, 588.3523762226105))
        b = np.array((1620.789098739624, 579.5307874679565))
        sample = np.array((1735.5, 585.5))
        a32, b32, sample32 = (value.astype(np.float32) for value in (a, b, sample))
        coefficients32 = np.array((a32[1]-b32[1], b32[0]-a32[0],
                                   a32[0]*b32[1]-a32[1]*b32[0]))
        coefficient_value = coefficients32 @ np.r_[sample32, np.float32(1)]
        endpoint_value = (b32[0]-a32[0])*(sample32[1]-a32[1])-(b32[1]-a32[1])*(sample32[0]-a32[0])
        # Equivalent float32 edge arithmetic can disagree about which side the
        # sample lies on, even though the shared endpoints are byte-identical.
        self.assertGreater(coefficient_value, 0)
        self.assertLess(endpoint_value, 0)
        def edge(first, second):
            reverse = tuple(first) > tuple(second)
            low, high = (second, first) if reverse else (first, second)
            values = np.array((low[1]-high[1], high[0]-low[0],
                               low[0]*high[1]-low[1]*high[0]))
            return -values if reverse else values
        forward, backward = edge(a, b), edge(b, a)
        np.testing.assert_array_equal(forward, -backward)
        include_forward = b[1] > a[1] or (b[1] == a[1] and b[0] < a[0])
        include_backward = a[1] > b[1] or (a[1] == b[1] and a[0] < b[0])
        self.assertNotEqual(include_forward, include_backward)
        for offset in (-1e-6, -1e-12, 0.0, 1e-12, 1e-6):
            point = np.r_[sample + (0, offset), 1.0]
            first, second = forward @ point, backward @ point
            self.assertEqual(first, -second)
            admitted = int(first > 0 or (first == 0 and include_forward))
            admitted += int(second > 0 or (second == 0 and include_backward))
            self.assertEqual(admitted, 1)

        c = np.array((1621.5831965208054, 577.4187386035919))
        d = np.array((1789.5203053951263, 590.4644250869751))
        for triangle in (np.array((a, b, c)), np.array((a, d, b))):
            low, high = np.floor(triangle.min(axis=0))-1, np.ceil(triangle.max(axis=0))+1
            extent = high-low
            relative = (triangle-low)/(2*extent)
            self.assertTrue(np.all(relative > 0))
            self.assertTrue(np.all(relative.sum(axis=1) < 1), 'support hypotenuse must stay outside original vertices')

    def test_software_shared_vertex_transform_is_precise_without_geometry_or_sample_changes(self):
        vertex = '#version 410\nvoid main(){gl_Position = projection * view * world;}'
        for renderer in ('Apple M1 Pro', 'llvmpipe', 'AMD Radeon', 'NVIDIA'):
            self.assertIs(capture.capture_receiver_vertex(vertex, renderer), vertex)
        adapted = capture.capture_receiver_vertex(vertex, 'Apple Software Renderer')
        self.assertEqual(adapted.replace('invariant gl_Position;\nprecise gl_Position;\n', ''), vertex)
        self.assertEqual(adapted.count('gl_Position = projection * view * world;'), 1)
        for changed in ('void main(){}', vertex + '\ngl_Position = another;'):
            with self.assertRaisesRegex(RuntimeError, 'vertex shader changed'):
                capture.capture_receiver_vertex(changed, 'Apple Software Renderer')

    def test_four_sample_resolve_is_only_for_apple_software_and_preserves_spatial_rgba(self):
        for renderer, scale in (("Apple Software Renderer", 2), ("Apple M1 Pro", 1),
                ("llvmpipe", 1), ("AMD Radeon", 1), ("NVIDIA", 1)):
            self.assertEqual(capture.capture_sample_scale(renderer), scale)
        raw = b'unchanged native MSAA bytes'
        self.assertIs(capture.resolve_capture_samples(raw, 1), raw)
        # Two neighbouring pixels, each with four differently coloured samples.
        samples = np.array([
            [[0, 1, 2, 255], [4, 5, 6, 255], [100, 101, 102, 255], [104, 105, 106, 255]],
            [[8, 9, 10, 255], [12, 13, 14, 255], [108, 109, 110, 255], [112, 113, 114, 255]],
        ], dtype=np.float32) / 255.0
        with patch.object(capture, 'SIZE', (2, 1)):
            resolved = capture.resolve_capture_samples(samples.tobytes(), 2)
        self.assertEqual(resolved, bytes((6, 7, 8, 255, 106, 107, 108, 255)))
        with self.assertRaisesRegex(ValueError, 'sample scale'):
            capture.resolve_capture_samples(raw, 3)

    def test_float_resolve_clamps_each_sample_and_rejects_nonfinite_input(self):
        samples = np.array([[[-2, 0, .5, 1], [2, .5, .5, 1]],
                            [[0, .5, .5, 1], [1, 1, .5, 1]]], dtype=np.float32)
        with patch.object(capture, 'SIZE', (1, 1)):
            self.assertEqual(capture.resolve_capture_samples(samples.tobytes(), 2), bytes((128, 128, 128, 255)))
            for invalid in (float('nan'), float('inf'), -float('inf')):
                samples[0, 0, 0] = invalid
                with self.assertRaisesRegex(ValueError, 'Nonfinite'):
                    capture.resolve_capture_samples(samples.tobytes(), 2)

    def test_fixed_pixel_plane_matches_the_actual_uploaded_camera(self):
        for target, yaw, pitch, height in (((0, 8, 61), 12, -10, 170),
                ((-12, 0, 10), -28, 7, 80)):
            projection, _eye = capture.camera(target, yaw, pitch, height)
            rows = projection.astype(np.float32).astype(np.float64).T
            z = -.1
            values = capture.capture_bed_uniforms(projection, z, (2800, 2200))
            self.assertEqual(values['u_captureViewport'], (2800, 2200))
            for x, y in ((0.5, 0.5), (1399.5, 731.5), (2799.5, 2199.5)):
                ndc = np.array([x / 2800 * 2 - 1, y / 2200 * 2 - 1, 1])
                point = np.array([np.dot(ndc, values['u_captureBedX']),
                                  np.dot(ndc, values['u_captureBedY']), z, 1])
                np.testing.assert_allclose((rows @ point)[:2], ndc[:2], atol=1e-12, rtol=0)
        perspective = np.eye(4, dtype=np.float32)
        perspective[2, 3] = -1
        with self.assertRaisesRegex(ValueError, 'orthographic camera'):
            capture.capture_bed_uniforms(perspective, -.1, (2800, 2200))

    def test_plane_adapter_keeps_other_drivers_shader_source_unchanged(self):
        fragment = 'uniform bool u_captureGrid;\nvec3 baseColour=f_color.rgb;\nlightSurface(f_vertex,f_normal,baseColour);'
        for renderer in ('Apple M1 Pro', 'llvmpipe', 'AMD Radeon', 'NVIDIA'):
            self.assertIs(capture.capture_receiver_fragment(fragment, renderer), fragment)
        adapted = capture.capture_receiver_fragment(fragment, 'Apple Software Renderer')
        self.assertIn('precise vec3 capturePosition=f_vertex;', adapted)
        self.assertIn('gl_FragCoord.xy/u_captureViewport', adapted)
        self.assertIn('lightSurface(capturePosition,captureNormal,baseColour)', adapted)

    def test_analytic_grid_is_bed_only_and_restores_path_palette_after_failure(self):
        events = []
        bed = Mock()
        paths = Mock()
        uniform = Mock(value=0)
        shader = {"u_captureGrid": uniform}
        def paint():
            events.append(("bed", uniform.value))
            if fail[0]: raise RuntimeError("failed grid draw")
        bed.render.side_effect = paint
        paths.render.side_effect = lambda: events.append(("paths", uniform.value))
        fail = [False]
        capture.render_receivers(bed, paths, shader)
        self.assertEqual(events, [("bed", 1), ("paths", 0)])
        fail[0] = True
        events.clear()
        with self.assertRaisesRegex(RuntimeError, "failed grid draw"):
            capture.render_receivers(bed, paths, shader)
        self.assertEqual(uniform.value, 0)
        self.assertFalse(any(event[0] == "paths" for event in events))

    def test_headless_windows_shader_tests_use_capture_cpu_renderer(self):
        module = Mock()
        directory = Mock()
        def context(**options):
            self.assertEqual(options, dict(standalone=True, require=410))
            self.assertEqual(capture.os.environ["GALLIUM_DRIVER"], "llvmpipe")
            self.assertEqual(capture.os.environ["LP_NUM_THREADS"], "1")
            return "context"
        with patch.object(capture.sys, "platform", "win32"), \
             patch.dict(capture.os.environ, {"GLCONTEXT_WIN_LIBGL": "/mesa/opengl32.dll"}, clear=True), \
             patch.object(capture.os, "add_dll_directory", return_value=directory, create=True) as add, \
             patch.dict(capture.sys.modules, {"moderngl": module}):
            module.create_context.side_effect = context
            self.assertEqual(capture.create_context(), ("context", directory))
            add.assert_called_once_with(str(capture.Path("/mesa/opengl32.dll").parent))
            directory.close.assert_not_called()

    def test_mesh_is_losslessly_the_reference_step_import(self):
        with np.load(capture.FIXTURE / "stealthburner.npz", allow_pickle=False) as data:
            count = len(data["triangles"])
            self.assertTrue(np.all(data["colours"][:, 3] == 1.0),
                            "the showcase single-pass render requires opaque CAD materials")
            packed = (struct.pack("<8sI", b"MPFHEAD2", count)
                      + data["triangles"].astype("<f4").tobytes()
                      + data["colours"].astype("<f4").tobytes()
                      + data["surfaces"].astype("<u4").tobytes())
        provenance = json.loads((capture.FIXTURE / "provenance.json").read_text())
        self.assertEqual(hashlib.sha256(packed).hexdigest(), provenance["stealthburner_mesh_sha256"])
        self.assertEqual(hashlib.sha256((capture.FIXTURE / "Voron_Design_Cube_v7.stl").read_bytes()).hexdigest(),
                         provenance["cube_sha256"])

    def test_saved_reference_has_five_outward_coloured_lights(self):
        value = json.loads((capture.FIXTURE / "lighting.json").read_text())
        lights = capture.validated_lights(value["toolhead_lights"])
        self.assertEqual(len(lights), 5)
        self.assertEqual([light["colour"] for light in lights], ["#b57b00"]*2 + ["#6d00ed"]*3)
        self.assertEqual([light["brightness"] for light in lights], [2.1, 1.55, 2.5, 2.5, 2.45])
        self.assertTrue(all(light["paint"] for light in lights))
        for light in lights:
            position, direction, _colour, _reach = capture.light_values(light)
            self.assertGreater(np.dot(np.asarray(position)-light["position"], direction), 0)

    def test_partial_cube_has_real_logo_contours_and_ninety_layers(self):
        segments = capture.cube_segments()
        z = np.unique(np.round(segments[:, :, 2], 3))
        self.assertEqual(len(z), 90)
        self.assertAlmostEqual(z.min(), .1, places=5)
        self.assertAlmostEqual(z.max(), 17.9, places=5)
        self.assertEqual(segments.shape[1:], (2, 3))
        self.assertGreater(len(segments), 1000)
        # A plain four-sided box would have no interior/recessed contour points.
        interior = segments[:, :, :2].reshape(-1, 2)
        self.assertGreater(np.count_nonzero(np.all((interior > 2) & (interior < 28), axis=1)), 100)

    def test_tube_normals_face_outwards_and_dimensions_match_filament(self):
        segment = np.array([[[0, 0, 0], [10, 0, 0]]], dtype=np.float32)
        triangles = capture.tubes(segment)
        normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        centers = triangles.mean(axis=1)
        self.assertTrue(np.all(np.sum(normals[:, 1:]*centers[:, 1:], axis=1) > 0))
        self.assertAlmostEqual(float(np.ptp(triangles[:, :, 1])), .42, places=6)
        self.assertAlmostEqual(float(np.ptp(triangles[:, :, 2])), .2, places=6)


if __name__ == "__main__":
    unittest.main()
