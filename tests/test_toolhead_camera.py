"""Delivered projection and rotated camera back-axis for all lighting owners."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
import numpy as np
from mpf.toolhead.ToolheadCamera import camera_view, apply_camera_view


class CameraTests(unittest.TestCase):
    def test_cropped_orthographic_rotation_and_perspective_use_actual_projection(self):
        view=np.eye(4);view[:3,:3]=[[0,0,-1],[0,1,0],[1,0,0]]
        projection=np.eye(4);projection[0,3]=.3
        camera=NS(getProjectionMatrix=lambda:NS(getData=lambda:projection),getInverseWorldTransformation=lambda:NS(getData=lambda:view))
        orthographic,direction=camera_view(camera)
        self.assertTrue(orthographic)
        np.testing.assert_array_equal(direction,[1,0,0])
        shader=Mock();apply_camera_view(shader,camera)
        shader.setUniformValue.assert_any_call('u_orthographic',1)
        shader.setUniformValue.assert_any_call('u_viewDirection',[1.,0.,0.])
        projection[3]=[0,0,-1,0]
        self.assertFalse(camera_view(camera)[0])
        apply_camera_view(shader,camera)
        shader.setUniformValue.assert_any_call('u_orthographic',0)
