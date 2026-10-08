"""Both packaged GL variants retain paint, alpha and depth-pass contracts."""
import configparser
from pathlib import Path
import unittest


class ToolheadShaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
        cls.source.read(Path(__file__).resolve().parents[1] / "mpf/toolhead/toolhead.shader")

    def fragments(self):
        for key in ("fragment", "fragment41core"):
            yield key, self.source["shaders"][key]

    def test_normal_render_is_default_and_depth_keeps_alpha_discard(self):
        self.assertEqual(self.source["defaults"]["u_depthOnly"], "0")
        for key, source in self.fragments():
            with self.subTest(key=key):
                discard = source.index("if (v_color.a <= 0.0) discard;")
                depth = source.index("if (u_depthOnly == 1)")
                lighting = source.index("vec3 normal = surfaceNormal();")
                self.assertLess(discard, depth)
                self.assertLess(depth, lighting)
                self.assertIn("return;", source[depth:lighting])

    def test_selected_surface_paint_and_emission_survive_all_light_culls(self):
        for key, source in self.fragments():
            with self.subTest(key=key):
                loop = source[source.index("for (int i = 0; i < 8; ++i)"):]
                paint = loop.index("baseColour = u_attachedPaint[i].rgb;")
                emission = loop.index("emission = u_attachedColour[i] * 0.35;")
                for rejection in ("dot(u_attachedColour[i], u_attachedColour[i]) <= 0.0",
                                  "squaredDistance >= u_attachedRange[i] * u_attachedRange[i]",
                                  "if (outward <= 0.0)"):
                    self.assertLess(paint, loop.index(rejection))
                    self.assertLess(emission, loop.index(rejection))

    def test_distant_or_dark_lights_skip_distance_and_backwards_lights_skip_highlight(self):
        for key, source in self.fragments():
            with self.subTest(key=key):
                loop = source[source.index("for (int i = 0; i < 8; ++i)"):]
                distance = loop.index("sqrt(squaredDistance)")
                self.assertLess(loop.index("dot(u_attachedColour[i], u_attachedColour[i]) <= 0.0"), distance)
                self.assertLess(loop.index("squaredDistance >= u_attachedRange[i] * u_attachedRange[i]"), distance)
                self.assertLess(loop.index("if (outward <= 0.0) continue;"), loop.index("vec3 halfLight"))
                self.assertIn("max(sqrt(squaredDistance), 0.001)", loop)

    def test_legacy_and_core_execute_identical_lighting_math(self):
        legacy = self.source["shaders"]["fragment"]
        core = self.source["shaders"]["fragment41core"]
        self.assertTrue(core.lstrip().startswith("#version 410"))
        # Declarations differ between GLSL versions; all functions must agree.
        legacy = legacy[legacy.index("float led("):]
        core = core[core.index("float led("):].replace("frag_color", "gl_FragColor")
        self.assertEqual(legacy, core)

    def test_master_disable_returns_painted_base_colour_before_all_lighting(self):
        self.assertEqual(self.source["defaults"]["u_lightingEnabled"], "1")
        for key, source in self.fragments():
            with self.subTest(key=key):
                branch = source[source.index("if (u_lightingEnabled == 0)"):source.index("vec3 normal = surfaceNormal();")]
                self.assertIn("colour = u_attachedPaint[i].rgb", branch)
                self.assertIn("vec4(colour, v_color.a * u_opacity)", branch)
                self.assertIn("return;", branch)
                self.assertNotIn("u_attachedColour", branch)
                self.assertNotIn("led(", branch)

    def test_opaque_prepass_does_not_change_colour_output_or_opacity_equation(self):
        for key, source in self.fragments():
            with self.subTest(key=key):
                self.assertIn("vec4(lit, v_color.a * u_opacity)", source)
                self.assertIn("mix(24.0, mix(4.0, 128.0, pow(1.0 - roughness, 4.0)), known)", source)
                self.assertIn("pow(max(dot(normal, halfLight), 0.0), 8.0)", source)
