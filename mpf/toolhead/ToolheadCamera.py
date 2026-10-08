"""View-ray uniforms from the delivered Cura camera, including cropped views."""
import numpy as np
from functools import lru_cache


@lru_cache(maxsize=32)
def _view_ray(dtype, shape, values):
    # Value keys also admit temporary cropped-camera wrappers and invalidate
    # when Cura mutates a camera matrix in place. Keep no camera/scene owner.
    view = np.frombuffer(values, dtype=np.dtype(dtype)).reshape(shape)
    direction = np.linalg.inv(view)[:3, 2]
    direction = direction / np.linalg.norm(direction)
    return tuple(direction)


@lru_cache(maxsize=32)
def _orthographic(dtype, values):
    return bool(np.allclose(np.frombuffer(values, dtype=np.dtype(dtype)), 0, atol=1e-7))


def camera_view(camera):
    projection = np.asarray(camera.getProjectionMatrix().getData())
    orthographic = _orthographic(projection.dtype.str, projection[3, :3].tobytes())
    view = np.asarray(camera.getInverseWorldTransformation().getData())
    direction = _view_ray(view.dtype.str, view.shape, view.tobytes())
    return orthographic, np.asarray(direction)


def apply_camera_view(shader, camera):
    orthographic, direction = camera_view(camera)
    shader.setUniformValue('u_orthographic', int(orthographic))
    shader.setUniformValue('u_viewDirection', direction.tolist())
