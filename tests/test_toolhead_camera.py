"""Delivered projection and rotated camera back-axis for all lighting owners."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import numpy as np
from mpf.toolhead.ToolheadCamera import camera_view, apply_camera_view


class CameraTests(unittest.TestCase):
    def test_value_cache_handles_wrappers_mutations_dtype_and_general_affine_views(self):
        from mpf.toolhead.ToolheadCamera import _view_ray, _orthographic
        _view_ray.cache_clear(); _orthographic.cache_clear()
        view=np.array([[2.,.2,.4,7.],[0.,3.,.1,8.],[.3,0.,4.,9.],[0.,0.,0.,1.]])
        projection=np.eye(4)
        def camera():
            return NS(getProjectionMatrix=lambda:NS(getData=lambda:projection),
                      getInverseWorldTransformation=lambda:NS(getData=lambda:view))
        actual=np.linalg.inv
        with patch('mpf.toolhead.ToolheadCamera.np.linalg.inv',wraps=actual) as inverse:
            for _ in range(5):
                orthographic,direction=camera_view(camera())
                expected=actual(view)[:3,2];expected/=np.linalg.norm(expected)
                np.testing.assert_array_equal(direction,expected)
                self.assertTrue(orthographic)
                direction[:]=0  # Returned arrays cannot corrupt the shared cache.
            self.assertEqual(inverse.call_count,1)
            view[0,2]=.8
            camera_view(camera());self.assertEqual(inverse.call_count,2)
            projection[3,:]=[0.,0.,-1.,0.]
            self.assertFalse(camera_view(camera())[0])
            self.assertEqual(inverse.call_count,2)
            view=view.astype(np.float32)
            camera_view(camera());self.assertEqual(inverse.call_count,3)
            for index in range(40):
                view[0,3]=index
                camera_view(camera())
        self.assertLessEqual(_view_ray.cache_info().currsize,32)
        singular=np.zeros((4,4))
        view=singular
        with self.assertRaises(np.linalg.LinAlgError):camera_view(camera())

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
