"""Canonical Z-up millimetre triangles and nozzle anchoring."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

MAX_TRIANGLES = 1_000_000
MAX_COORDINATE = 10_000


@dataclass(frozen=True, eq=False)
class ToolheadMesh:
    triangles: np.ndarray
    colours: np.ndarray
    surfaces: np.ndarray

    @property
    def automatic_tip(self):
        points = self.triangles.reshape(-1, 3)
        low = float(points[:, 2].min())
        bottom = points[points[:, 2] <= low + 0.001]
        return (float((bottom[:, 0].min() + bottom[:, 0].max()) / 2),
                float((bottom[:, 1].min() + bottom[:, 1].max()) / 2), low)


def mesh_from_arrays(triangles, colours=None, surfaces=None):
    points = np.array(triangles, dtype=np.float32, copy=True).reshape(-1, 3, 3)
    if not 0 < len(points) <= MAX_TRIANGLES:
        raise ValueError("Model must contain 1–1,000,000 triangles")
    if not np.isfinite(points).all() or np.abs(points).max() > MAX_COORDINATE:
        raise ValueError("Model contains invalid or out-of-range coordinates")
    if np.ptp(points.reshape(-1, 3), axis=0).max() > 2_000:
        raise ValueError("Toolhead model exceeds 2,000 mm; export STL in millimetres")
    normals = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    if not np.any(np.linalg.norm(normals, axis=1) > 1e-9):
        raise ValueError("Model has no usable surfaces")
    if colours is None:
        colours = np.tile((0.72, 0.74, 0.78, 1.0), (len(points), 1))
    colours = np.array(colours, dtype=np.float32, copy=True).reshape(-1, 4)
    if len(colours) != len(points) or not np.isfinite(colours).all():
        raise ValueError("Model colours do not match its triangles")
    colours = np.clip(colours, 0, 1)
    surfaces = np.arange(len(points), dtype=np.uint32) if surfaces is None else np.array(surfaces, copy=True)
    if surfaces.shape != (len(points),) or not np.isfinite(surfaces).all() or np.any(surfaces < 0) or np.any(surfaces > MAX_TRIANGLES) or np.any(surfaces != np.floor(surfaces)):
        raise ValueError("Model contains invalid surface identifiers")
    surfaces = surfaces.astype(np.uint32)
    points.flags.writeable = colours.flags.writeable = surfaces.flags.writeable = False
    return ToolheadMesh(points, colours, surfaces)


def valid_tip(value):
    try:
        point = tuple(float(v) for v in value)
        return point if len(point) == 3 and all(np.isfinite(v) and abs(v) <= MAX_COORDINATE for v in point) else None
    except (TypeError, ValueError):
        return None


def default_mesh():
    triangles = []
    for index in range(16):
        a, b = index * np.pi / 8, (index + 1) * np.pi / 8
        p, q = (3 * np.cos(a), 3 * np.sin(a), 10), (3 * np.cos(b), 3 * np.sin(b), 10)
        triangles.extend((((0, 0, 0), q, p), ((0, 0, 10), p, q)))
    return mesh_from_arrays(triangles)


def camera_projection(mesh, yaw, pitch, width, height, zoom=1):
    """Orthographic preview coordinates; picking uses this identical transform."""
    points = mesh.triangles.reshape(-1, 3)
    centre = (points.min(axis=0) + points.max(axis=0)) / 2
    yaw, pitch = np.deg2rad((yaw, pitch))
    right = np.array((np.cos(yaw), np.sin(yaw), 0))
    up = np.array((-np.sin(yaw) * np.sin(pitch), np.cos(yaw) * np.sin(pitch), np.cos(pitch)))
    depth = np.cross(right, up)
    matrix = np.stack((right, -up, depth), axis=1)
    offset = points - centre
    # Three-column rotation does not need BLAS. Cura's bundled macOS BLAS
    # can stall on large matrix products in the preview worker thread.
    rotated = np.stack([offset[:, 0]*matrix[0, i] + offset[:, 1]*matrix[1, i]
                        + offset[:, 2]*matrix[2, i] for i in range(3)], axis=1)
    radius = max(float(np.linalg.norm(offset, axis=1).max()), 1)
    scale = min(width, height) * 0.42 * zoom / radius
    projected = rotated * (scale, scale, 1) + (width / 2, height / 2, 0)
    return projected.reshape(-1, 3, 3), centre, matrix, scale


def visible_triangles(projected, width=None, height=None, colours=None):
    """Painter order shared by the preview and its surface picker."""
    points = projected[:, :, :2]
    a, b = points[:, 1]-points[:, 0], points[:, 2]-points[:, 0]
    visible = np.abs(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0]) >= .15
    if colours is not None: visible &= colours[:, 3] > 0
    if width is not None and height is not None:
        visible &= (points.max(axis=1)[:, 0] >= 0) & (points.min(axis=1)[:, 0] <= width)
        visible &= (points.max(axis=1)[:, 1] >= 0) & (points.min(axis=1)[:, 1] <= height)
    ids = np.flatnonzero(visible)
    return ids[np.argsort(projected[ids, :, 2].mean(axis=1), kind="stable")]


def pick_projected(mesh, projected, x, y, nearest=False, surface=False):
    a, b, c = projected[:, 0], projected[:, 1], projected[:, 2]
    denominator = (b[:, 1]-c[:, 1])*(a[:, 0]-c[:, 0]) + (c[:, 0]-b[:, 0])*(a[:, 1]-c[:, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        u = ((b[:, 1]-c[:, 1])*(x-c[:, 0])+(c[:, 0]-b[:, 0])*(y-c[:, 1])) / denominator
        v = ((c[:, 1]-a[:, 1])*(x-c[:, 0])+(a[:, 0]-c[:, 0])*(y-c[:, 1])) / denominator
    with np.errstate(invalid="ignore"):
        w = 1-u-v
    order = np.flatnonzero((np.abs(denominator) > 1e-12) & (mesh.colours[:, 3] > 0)) if nearest else visible_triangles(projected, colours=mesh.colours)
    hits = order[((u >= 0) & (v >= 0) & (w >= 0) & np.isfinite(u))[order]]
    if not len(hits):
        return None
    # Pick the face actually painted at this pixel, including overlapping
    # surfaces and triangles that vanish at the current preview scale.
    depths = u[hits]*a[hits, 2] + v[hits]*b[hits, 2] + w[hits]*c[hits, 2]
    hit = hits[np.argmax(depths)] if nearest else hits[-1]
    point = tuple(float(v) for v in (u[hit]*mesh.triangles[hit, 0]+v[hit]*mesh.triangles[hit, 1]+w[hit]*mesh.triangles[hit, 2]))
    if not surface: return point
    face = mesh.triangles[hit]
    normal = np.cross(face[1]-face[0], face[2]-face[0])
    normal /= max(float(np.linalg.norm(normal)), 1e-9)
    return point, tuple(float(v) for v in normal), int(mesh.surfaces[hit])


def preview_buffer(mesh):
    """Pack a mesh once; camera changes never rebuild or upload geometry."""
    points = mesh.triangles.reshape(-1, 3)
    centre = (points.min(axis=0) + points.max(axis=0)) / 2
    radius = max(float(np.linalg.norm(points-centre, axis=1).max()), 1)
    normal = np.cross(mesh.triangles[:, 1]-mesh.triangles[:, 0], mesh.triangles[:, 2]-mesh.triangles[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-9)[:, None]
    # Broadcasting fills the final GPU layout directly. Mixing float32 with
    # uint32 surface IDs in column_stack otherwise promotes the whole buffer
    # to float64, then requires another full copy to convert it back.
    packed = np.empty((len(mesh.triangles), 3, 11), dtype=np.float32)
    packed[:, :, :3] = mesh.triangles
    packed[:, :, 3:6] = normal[:, None, :]
    packed[:, :, 6:10] = mesh.colours[:, None, :]
    packed[:, :, 10] = mesh.surfaces[:, None]
    return packed.tobytes(), centre, radius, len(points)


def preview_camera(centre, radius, yaw, pitch, width, height, zoom):
    yaw, pitch = np.deg2rad((yaw, pitch))
    right = np.array((np.cos(yaw), np.sin(yaw), 0))
    up = np.array((-np.sin(yaw)*np.sin(pitch), np.cos(yaw)*np.sin(pitch), np.cos(pitch)))
    matrix = np.stack((right, -up, np.cross(right, up)), axis=1)
    return centre, matrix, min(width, height) * .42 * zoom / radius
