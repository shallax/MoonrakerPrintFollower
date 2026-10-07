"""The showcase keeps the chosen CAD palette, lights and physical dimensions."""
import hashlib
import json
import struct
import unittest

import numpy as np

from tools import capture_toolhead as capture


class ToolheadCaptureTests(unittest.TestCase):
    def test_mesh_is_losslessly_the_reference_step_import(self):
        with np.load(capture.FIXTURE / "stealthburner.npz", allow_pickle=False) as data:
            count = len(data["triangles"])
            self.assertTrue(np.all(data["colours"][:, 3] == 1.0),
                            "the showcase single-pass render requires opaque CAD materials")
            packed = (struct.pack("<8sI", b"MPFHEAD2", count)
                      + data["triangles"].astype("<f4").tobytes()
                      + data["colours"].astype("<f4").tobytes()
                      + data["surfaces"].astype("<u4").tobytes())
        provenance = json.loads((capture.FIXTURE / "provenance.json").read_text())
        self.assertEqual(hashlib.sha256(packed).hexdigest(), provenance["stealthburner_mesh_sha256"])
        self.assertEqual(hashlib.sha256((capture.FIXTURE / "Voron_Design_Cube_v7.stl").read_bytes()).hexdigest(),
                         provenance["cube_sha256"])

    def test_saved_reference_has_five_outward_coloured_lights(self):
        value = json.loads((capture.FIXTURE / "lighting.json").read_text())
        lights = capture.validated_lights(value["toolhead_lights"])
        self.assertEqual(len(lights), 5)
        self.assertEqual([light["colour"] for light in lights], ["#b57b00"]*2 + ["#6d00ed"]*3)
        self.assertEqual([light["brightness"] for light in lights], [2.1, 1.55, 2.5, 2.5, 2.45])
        self.assertTrue(all(light["paint"] for light in lights))
        for light in lights:
            position, direction, _colour, _reach = capture.light_values(light)
            self.assertGreater(np.dot(np.asarray(position)-light["position"], direction), 0)

    def test_partial_cube_has_real_logo_contours_and_ninety_layers(self):
        segments = capture.cube_segments()
        z = np.unique(np.round(segments[:, :, 2], 3))
        self.assertEqual(len(z), 90)
        self.assertAlmostEqual(z.min(), .1, places=5)
        self.assertAlmostEqual(z.max(), 17.9, places=5)
        self.assertEqual(segments.shape[1:], (2, 3))
        self.assertGreater(len(segments), 1000)
        # A plain four-sided box would have no interior/recessed contour points.
        interior = segments[:, :, :2].reshape(-1, 2)
        self.assertGreater(np.count_nonzero(np.all((interior > 2) & (interior < 28), axis=1)), 100)

    def test_tube_normals_face_outwards_and_dimensions_match_filament(self):
        segment = np.array([[[0, 0, 0], [10, 0, 0]]], dtype=np.float32)
        triangles = capture.tubes(segment)
        normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        centers = triangles.mean(axis=1)
        self.assertTrue(np.all(np.sum(normals[:, 1:]*centers[:, 1:], axis=1) > 0))
        self.assertAlmostEqual(float(np.ptp(triangles[:, :, 1])), .42, places=6)
        self.assertAlmostEqual(float(np.ptp(triangles[:, :, 2])), .2, places=6)


if __name__ == "__main__":
    unittest.main()
