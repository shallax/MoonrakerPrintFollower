"""Sparse asset-bound opacity overrides; source CAD colours stay immutable."""
from collections.abc import Mapping

import numpy as np

from .ToolheadMaterials import unit_value

MAX_OPACITY_ENTRIES = 2048


def opacity_overrides(value, mesh=None, *, bodies=False):
    if not isinstance(value, Mapping): return {}
    result = {}
    present = (mesh.present_bodies if bodies else mesh.present_surfaces) if mesh is not None else None
    for key, raw in value.items():
        if type(key) is int: identity = key
        elif type(key) is str and key.isascii() and key.isdecimal() and len(key) <= 8: identity = int(key)
        else: continue
        number = "imported" if type(raw) is str and raw == "imported" else unit_value(raw)
        if not 0 <= identity < 16777216 or number is None or (present is not None and identity not in present): continue
        result[str(identity)] = number
        if len(result) == MAX_OPACITY_ENTRIES: break
    return result


def opacity_colours(mesh, bodies=None, faces=None):
    """Face overrides win over body overrides, then original STEP alpha."""
    bodies = opacity_overrides(bodies, mesh, bodies=True)
    faces = opacity_overrides(faces, mesh)
    if not bodies and not faces: return mesh.colours
    colours = mesh.colours.copy()
    # One vectorised lookup per identity domain, not a mesh scan per override.
    for identities, values in ((mesh.body_ids, bodies), (mesh.surfaces, faces)):
        if not values: continue
        keys = np.array(sorted(map(int, values)), dtype=np.uint32)
        indices = np.searchsorted(keys, identities)
        bounded = np.minimum(indices, len(keys)-1)
        mask = (indices < len(keys)) & (keys[bounded] == identities)
        alpha = np.array([-1. if values[str(int(key))] == "imported" else values[str(int(key))] for key in keys], dtype=np.float32)
        chosen = alpha[bounded[mask]]
        colours[mask, 3] = np.where(chosen < 0, mesh.colours[mask, 3], chosen)
    colours.flags.writeable = False
    return colours


def selection_mask(mesh, bodies, faces):
    return np.isin(mesh.body_ids, tuple(bodies)) | np.isin(mesh.surfaces, tuple(faces))


def opacity_preview(mesh, colours, bodies, faces):
    """Temporary selection tint and recoverable invisible geometry in edit mode."""
    result = colours.copy()
    result[result[:, 3] == 0, 3] = .12
    selected = selection_mask(mesh, bodies, faces)
    result[selected, :3] = result[selected, :3]*.3 + np.array((.1, .8, 1.))*.7
    result.flags.writeable = False
    return result
