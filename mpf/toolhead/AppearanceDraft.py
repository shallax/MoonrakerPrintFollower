# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Separate model import jobs and named appearance transactions from Qt presentation.

"""Named appearance values and checkpoints; no QObject, file publication or worker."""
from copy import deepcopy
from dataclasses import dataclass, field


@dataclass
class AppearanceDraft:
    texts: list = field(default_factory=lambda: ["0", "0", "0"])
    manual: bool = False
    lights: list = field(default_factory=list)
    surface_detail: float = .35
    rotors: list = field(default_factory=list)
    material_overrides: dict = field(default_factory=dict)
    surface_materials: dict = field(default_factory=dict)
    body_opacity: dict = field(default_factory=dict)
    face_opacity: dict = field(default_factory=dict)
    body_colours: dict = field(default_factory=dict)
    face_colours: dict = field(default_factory=dict)
    body_materials: dict = field(default_factory=dict)
    body_finishes: dict = field(default_factory=dict)
    face_finishes: dict = field(default_factory=dict)

    def checkpoint(self, identity, mesh):
        return AppearanceCheckpoint(identity, mesh, deepcopy(self))

    def reset_model(self):
        # Global material/detail preferences survive replacing a mesh, as before.
        for name in ("surface_materials", "body_opacity", "face_opacity", "body_colours",
                     "face_colours", "body_materials", "body_finishes", "face_finishes"):
            setattr(self, name, {})
        self.lights, self.rotors = [], []


@dataclass(frozen=True)
class AppearanceCheckpoint:
    identity: object
    mesh: object
    draft: AppearanceDraft

    def restore(self, identity, mesh):
        if identity != self.identity or mesh is not self.mesh:
            return None
        return deepcopy(self.draft)
