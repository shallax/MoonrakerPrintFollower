"""Actual legacy Qt compiler: no dynamic sampler indexing or core-only lookup."""
import configparser
import os
from pathlib import Path
import sys
import unittest

os.environ['QT_QPA_PLATFORM'] = 'cocoa' if sys.platform == 'darwin' else 'offscreen'
from PyQt6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from PyQt6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram

from mpf.toolhead.ToolheadShadowShader import head_fragment, scene_fragment


class ShadowLegacyCompilerTests(unittest.TestCase):
    def test_actual_glsl120_programs_link_in_legacy_context(self):
        app = QGuiApplication.instance() or QGuiApplication([])
        self.assertIsNotNone(app)
        fmt = QSurfaceFormat(); fmt.setVersion(2, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        context = QOpenGLContext(); context.setFormat(fmt)
        available = context.create()
        surface = QOffscreenSurface(); surface.setFormat(context.format()); surface.create()
        available = available and context.makeCurrent(surface)
        if not available:
            if os.environ.get('MPF_REQUIRE_OFFSCREEN_GL') == '1':
                self.fail('Required legacy shadow compiler context unavailable')
            self.skipTest('Legacy GL unavailable')
        try:
            for name, adapter in (('toolhead.shader', head_fragment), ('scene-lighting.shader', scene_fragment)):
                parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
                parser.read(Path(__file__).resolve().parents[1] / 'mpf/toolhead' / name)
                source = parser['shaders']
                shader = QOpenGLShaderProgram()
                try:
                    self.assertTrue(shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
                        '#version 120\n'+source['vertex']), shader.log())
                    self.assertTrue(shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
                        '#version 120\n'+adapter(source['fragment'], enabled=True, legacy=True)), shader.log())
                    self.assertTrue(shader.link(), shader.log())
                finally:
                    shader.removeAllShaders()
        finally:
            context.doneCurrent(); surface.destroy()
