# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Exercise bed-mesh geometry directly without scene-node construction.

import unittest
try:
    import numpy as np
    from mpf.geometry.BedMeshGeometry import BedMeshGeometry
except ImportError:
    np = None


@unittest.skipIf(np is None, "bed-mesh geometry requires NumPy")
class BedMeshGeometryTests(unittest.TestCase):
    def test_arrays_have_physical_coordinates_and_explicit_probe_boundary(self):
        snapshot = dict(rows=2, columns=2, values=[0, 1, 2, 3],
                        xMin=0, xMax=100, yMin=0, yMax=100, minimum=0, maximum=3)
        mesh = BedMeshGeometry.build(snapshot, 100, 100, False)
        np.testing.assert_allclose(mesh.vertices[:4],
                                   [[-50, .035, 50], [50, 20.035, 50],
                                    [-50, 40.035, -50], [50, 60.035, -50]], rtol=1e-6)
        np.testing.assert_array_equal(mesh.indices[:2], [[0, 2, 1], [1, 2, 3]])
        self.assertEqual(mesh.vertices.dtype, np.float32)
        self.assertEqual(mesh.indices.dtype, np.int32)
        self.assertEqual(mesh.colours.shape, (20, 4))
        self.assertEqual(mesh.indices.shape, (10, 3))
        np.testing.assert_allclose(mesh.colours[4:], np.tile([1, .353, 0, .94], (16, 1)))
        self.assertEqual(snapshot['values'], [0, 1, 2, 3])
        self.assertIsNone(BedMeshGeometry.build({}, 100, 100, False))
