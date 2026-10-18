# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead
# 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0

"""Compile only the reflection traversal needed by a CAD batch."""
from ..resources.PluginPaths import plugin_path
from .ToolheadUniformCache import DeliveredUniformCache


def create_toolhead_shader():
    return _create_shader(opaque=False, full=False)


def create_opaque_shader():
    return _create_shader(opaque=True, full=False)


def create_hierarchy_shader(*, opaque=False):
    return _create_shader(opaque=opaque, full=True)


def _create_shader(*, opaque, full):
    from UM.View.GL.OpenGLContext import OpenGLContext
    from UM.View.GL.ShaderProgram import ShaderProgram

    class SpecializedShader(DeliveredUniformCache, ShaderProgram):
        def setFragmentShader(self, source):
            if full:
                source = source.replace("#define MPF_USE_FULL_HIERARCHY 0",
                                        "#define MPF_USE_FULL_HIERARCHY 1")
            if opaque:
                source = source.replace("if (v_color.a <= 0.0) discard;", "")
            return super().setFragmentShader(source)

    shader = SpecializedShader()
    shader.load(plugin_path("toolhead", "toolhead.shader"),
                version="" if OpenGLContext.isLegacyOpenGL() else "41core")
    return shader
