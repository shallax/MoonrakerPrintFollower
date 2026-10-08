"""View-ray uniforms from the delivered Cura camera, including cropped views."""
import numpy as np


def camera_view(camera):
    projection = np.asarray(camera.getProjectionMatrix().getData())
    orthographic = bool(np.allclose(projection[3, :3], 0, atol=1e-7))
    view = np.asarray(camera.getInverseWorldTransformation().getData())
    direction = np.linalg.inv(view)[:3, 2]
    return orthographic, direction / np.linalg.norm(direction)


def apply_camera_view(shader, camera):
    orthographic, direction = camera_view(camera)
    shader.setUniformValue('u_orthographic', int(orthographic))
    shader.setUniformValue('u_viewDirection', direction.tolist())
