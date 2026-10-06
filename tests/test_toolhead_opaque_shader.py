"""Opaque variants use public shader APIs and the unchanged shared light math."""
import configparser
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mpf.toolhead.ToolheadOpaqueShader import create_opaque_shader


class OpaqueShaderTests(unittest.TestCase):
    def test_legacy_and_core_preserve_bindings_and_only_remove_discard(self):
        for legacy in (False, True):
            recorded = {}
            class Shader:
                def load(self, path, version, recorded=recorded):
                    source = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
                    source.read(path)
                    recorded.update(version=version, source=source)
                    self.setFragmentShader(source["shaders"]["fragment" + version])
                def setFragmentShader(self, source, recorded=recorded):
                    recorded["fragment"] = source
                    return True
            with self.subTest(legacy=legacy), patch.dict(sys.modules, {
                "UM.View.GL.OpenGLContext": SimpleNamespace(OpenGLContext=SimpleNamespace(isLegacyOpenGL=lambda legacy=legacy: legacy)),
                "UM.View.GL.ShaderProgram": SimpleNamespace(ShaderProgram=Shader),
            }):
                shader = create_opaque_shader()
                version = "" if legacy else "41core"
                original = recorded["source"]["shaders"]["fragment" + version]
                self.assertEqual(recorded["version"], version)
                self.assertEqual(recorded["fragment"], original.replace("if (v_color.a <= 0.0) discard;", ""))
                self.assertNotIn("discard", recorded["fragment"])
                self.assertTrue(shader.setFragmentShader("unchanged"))
                self.assertEqual(recorded["fragment"], "unchanged")
