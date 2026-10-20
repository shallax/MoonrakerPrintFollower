# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead
# 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0

"""Opaque variants use public shader APIs and the unchanged shared light math."""
import configparser
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mpf.toolhead.ToolheadOpaqueShader import create_opaque_shader, create_hierarchy_shader


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

    def test_hierarchy_variants_specialize_only_requested_traversal_and_opacity(self):
        for opaque in (False, True):
            recorded = {}
            class Shader:
                def load(self, path, version, recorded=recorded):
                    source = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
                    source.read(path)
                    recorded.update(version=version, source=source)
                    self.setFragmentShader(source["shaders"]["fragment" + version])
                def setFragmentShader(self, source, recorded=recorded):
                    recorded["fragment"] = source
                    return "compiler-result"
            with self.subTest(opaque=opaque), patch.dict(sys.modules, {
                "UM.View.GL.OpenGLContext": SimpleNamespace(OpenGLContext=SimpleNamespace(isLegacyOpenGL=lambda: False)),
                "UM.View.GL.ShaderProgram": SimpleNamespace(ShaderProgram=Shader),
            }):
                shader = create_hierarchy_shader(opaque=opaque)
                original = recorded["source"]["shaders"]["fragment41core"]
                expected = original.replace("#define MPF_USE_FULL_HIERARCHY 0", "#define MPF_USE_FULL_HIERARCHY 1")
                if opaque: expected = expected.replace("if (v_color.a <= 0.0) discard;", "")
                self.assertEqual(recorded["version"], "41core")
                self.assertEqual(recorded["fragment"], expected)
                self.assertNotIn("#define MPF_USE_FULL_HIERARCHY 0", expected)
                self.assertEqual("if (v_color.a <= 0.0) discard;" in expected, not opaque)
                # Shader reload/recompile must retain the selected specialization.
                self.assertEqual(shader.setFragmentShader(original), "compiler-result")
                self.assertEqual(recorded["fragment"], expected)
                self.assertEqual(recorded["source"]["defaults"]["u_fullhierarchy"], "4")
                self.assertEqual(recorded["source"]["defaults"]["u_fullhierarchyenabled"], "0")
