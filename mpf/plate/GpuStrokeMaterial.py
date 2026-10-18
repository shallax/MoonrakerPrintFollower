# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-18 — 6.0.0: Keep Qt materials separate from reusable vertex packing.
# 2026-10-01 [d8cf321] Revert "mpf's subpackages are capitalised"
# 2026-09-30 [f425547] mpf's subpackages are capitalised
# 2026-09-27 [43ab36d] Accelerate follower rendering with retained GPU shader geometry

"""Pixel-width capsule strokes expanded and antialiased by the GPU."""


import struct
from PyQt6.QtGui import QColor
from PyQt6.QtQuick import QSGMaterial, QSGMaterialType, QSGMaterialShader, QSGGeometry

from ..resources.PluginPaths import plugin_path

TYPE = QSGMaterialType()
# Qt owns the native shader; retain its Python virtual-method wrapper.
SHADERS = []


class FollowerStrokeShader(QSGMaterialShader):
    def __init__(self):
        super().__init__()
        for stage, path in (
            (self.Stage.VertexStage, plugin_path("resources", "shaders", "stroke.vert.qsb")),
            (self.Stage.FragmentStage, plugin_path("resources", "shaders", "stroke.frag.qsb")),
        ):
            self.setShaderFileName(stage, path)

    def updateUniformData(self, state, new, old):
        m = state.combinedMatrix()
        colour = new.colour
        alpha = colour.alphaF() * state.opacity()
        values = (
            list(m.data())
            + [colour.redF() * alpha, colour.greenF() * alpha, colour.blueF() * alpha, alpha]
            + [
                new.width * state.devicePixelRatio() / 2,
                new.viewport[0] * state.devicePixelRatio() / 2,
                new.viewport[1] * state.devicePixelRatio() / 2,
                float(new.aa),
            ]
            + [float(new.rounded), new.split, float(new.clip), float(new.physical)]
        )
        values += list(new.colour_options)
        for ink in new.palette:
            values += [ink.redF(), ink.greenF(), ink.blueF(), ink.alphaF()]
        data = struct.pack("<96f", *values)
        state.uniformData().replace(0, len(data), data)
        return True


class FollowerStrokeMaterial(QSGMaterial):
    def __init__(self, colour, width=1, aa=False, rounded=True):
        super().__init__()
        self.colour = colour
        self.width = width
        self.aa = aa
        self.rounded = rounded
        self.viewport = (556, 556)
        self.split = 0.0
        self.clip = False
        self.physical = False
        self.colour_options = (-1., 0., 0., .2)
        self.palette = tuple(QColor("#888888") for _ in range(16))
        self.setFlag(self.Flag.Blending, True)
        self.setFlag(self.Flag.RequiresFullMatrix, True)

    def type(self):
        return TYPE

    def compare(self, other):
        a = (self.colour.rgba(), self.width, self.aa, self.rounded, self.viewport, self.split, self.clip, self.physical, self.colour_options, tuple(c.rgba() for c in self.palette))
        b = (other.colour.rgba(), other.width, other.aa, other.rounded, other.viewport, other.split, other.clip, other.physical, other.colour_options, tuple(c.rgba() for c in other.palette))
        return (a > b) - (a < b)

    def createShader(self, mode):
        shader = FollowerStrokeShader()
        SHADERS.append(shader)
        return shader


ATTR = QSGGeometry.AttributeSet(
    [
        QSGGeometry.Attribute.create(0, 2, QSGGeometry.Type.FloatType.value, True),
        QSGGeometry.Attribute.create(1, 2, QSGGeometry.Type.FloatType.value),
        QSGGeometry.Attribute.create(2, 2, QSGGeometry.Type.FloatType.value),
        QSGGeometry.Attribute.create(3, 2, QSGGeometry.Type.FloatType.value),
        QSGGeometry.Attribute.create(4, 2, QSGGeometry.Type.FloatType.value),
    ],
    40,
)
