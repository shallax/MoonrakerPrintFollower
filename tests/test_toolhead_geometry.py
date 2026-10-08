"""Canonical toolhead geometry, surface picking and durable local import contracts."""
import hashlib
import os
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

from mpf.geometry.ToolheadGeometry import (
    MAX_TRIANGLES, camera_projection, default_mesh, mesh_from_arrays,
    pick_projected, preview_buffer, valid_tip, visible_triangles,
)
from mpf.toolhead.ToolheadAssetStore import HEADER, MAGIC, ToolheadAssetStore
from mpf.toolhead.ToolheadImport import read_stl


def binary_stl(triangles, header=b"solid misleading binary header"):
    body = bytearray(header.ljust(80, b"\0")[:80])
    body.extend(struct.pack("<I", len(triangles)))
    for triangle in triangles:
        body.extend(struct.pack("<12fH", 0, 0, 0,
                                *(coordinate for point in triangle for coordinate in point), 0))
    return bytes(body)


class ToolheadGeometryTests(unittest.TestCase):
    def test_cad_face_smoothing_is_area_weighted_and_shared_by_packing(self):
        angle = np.deg2rad(20)
        mesh = mesh_from_arrays([
            ((0, 0, 0), (1, 0, 0), (0, 0, 1)),
            ((0, 0, 0), (0, 0, 1), (-2*np.cos(angle), 2*np.sin(angle), 0)),
        ], surfaces=[7, 7])
        expected = np.array([-2*np.sin(angle), -1-2*np.cos(angle), 0])
        expected /= np.linalg.norm(expected)
        normals = mesh.vertex_normals
        np.testing.assert_allclose(normals[0, [0, 2]], [expected]*2, atol=1e-6)
        np.testing.assert_array_equal(normals[0, [0, 2]], normals[1, [0, 1]])
        np.testing.assert_array_equal(normals[0, 1], [0, -1, 0])
        packed = np.frombuffer(preview_buffer(mesh)[0], np.float32).reshape(2, 3, 18)
        np.testing.assert_array_equal(packed[:, :, 3:6], normals)
        self.assertIs(normals, mesh.vertex_normals)
        self.assertFalse(normals.flags.writeable)

    def test_normal_budget_falls_back_before_welding_and_packing_accepts_broadcast(self):
        mesh = mesh_from_arrays([((0, 0, 0), (1, 0, 0), (0, 0, 1))]*2)
        with patch('mpf.geometry.ToolheadGeometry.NORMAL_WORK_BYTES', 512), patch('mpf.geometry.ToolheadGeometry.np.unique', side_effect=AssertionError('welding ran')):
            normals = mesh.vertex_normals
        self.assertEqual(normals.strides[1], 0)
        self.assertFalse(normals.flags.writeable)
        np.testing.assert_array_equal(normals, [[[0, -1, 0]]*3]*2)
        packed = np.frombuffer(preview_buffer(mesh)[0], np.float32).reshape(2, 3, 18)
        np.testing.assert_array_equal(packed[:, :, 3:6], normals)

    def test_smoothing_never_crosses_face_body_or_material_boundaries(self):
        angle = np.deg2rad(20)
        triangles = [((0, 0, 0), (1, 0, 0), (0, 0, 1)),
                     ((0, 0, 0), (0, 0, 1), (-np.cos(angle), np.sin(angle), 0))]
        metadata = {"materials": [dict(name="A", description="", source="unknown"),
                                  dict(name="B", description="", source="unknown")],
                    "bodies": [dict(name="A", source="unknown", centre=None, axis=None),
                               dict(name="B", source="unknown", centre=None, axis=None)]}
        for fields in ({"surfaces": [7, 8]}, {"body_ids": [0, 1]}, {"material_ids": [0, 1]}):
            with self.subTest(fields=fields):
                values = dict(surfaces=[7, 7], metadata=metadata); values.update(fields)
                mesh = mesh_from_arrays(triangles, **values)
                np.testing.assert_array_equal(mesh.vertex_normals[0], [[0, -1, 0]]*3)
                np.testing.assert_allclose(mesh.vertex_normals[1], [[-np.sin(angle), -np.cos(angle), 0]]*3, atol=1e-6)

    def test_folded_faces_and_degenerate_triangles_retain_safe_local_normals(self):
        mesh = mesh_from_arrays([
            ((0, 0, 0), (1, 0, 0), (0, 0, 1)),
            ((0, 0, 0), (0, 0, 1), (0, 1, 0)),
            ((0, 0, 0), (0, 0, 0), (0, 0, 0)),
        ], surfaces=[7, 7, 7])
        np.testing.assert_array_equal(mesh.vertex_normals[0], [[0, -1, 0]]*3)
        np.testing.assert_array_equal(mesh.vertex_normals[1], [[-1, 0, 0]]*3)
        np.testing.assert_array_equal(mesh.vertex_normals[2], np.zeros((3, 3)))

    def test_preview_vertex_layout_preserves_colours_normals_and_surface_ids(self):
        mesh = mesh_from_arrays(
            [((0, 0, 0), (2, 0, 0), (0, 2, 0)),
             ((0, 0, 0), (0, 0, 2), (2, 0, 0))],
            [(0, .25, 1, .75), (.5, 0, .25, 1)], [0, MAX_TRIANGLES])
        body, centre, radius, vertices, _layout = preview_buffer(mesh)
        self.assertEqual(vertices, 6)
        self.assertEqual(len(body), vertices * 72)
        packed = np.frombuffer(body, dtype=np.float32).reshape(2, 3, 18)
        np.testing.assert_array_equal(packed[:, :, :3], mesh.triangles[[1,0]])
        self.assertEqual(_layout[0], 3)
        self.assertEqual(_layout[1][0][:3], (3,3,0))
        for index, normal in enumerate(((0, 1, 0), (0, 0, 1))):
            np.testing.assert_array_equal(packed[index, :, 3:6], [normal] * 3)
            np.testing.assert_array_equal(packed[index, :, 6:10], [mesh.colours[1-index]] * 3)
            np.testing.assert_array_equal(packed[index, :, 10], [mesh.surfaces[1-index]] * 3)
        np.testing.assert_array_equal(centre, [1, 1, 1])
        self.assertAlmostEqual(radius, 3 ** .5)

    def test_preview_degenerate_faces_have_finite_zero_normals(self):
        mesh = mesh_from_arrays([((0, 0, 0), (0, 0, 0), (0, 0, 0)),
                                 ((0, 0, 0), (.1, 0, 0), (0, .1, 0))])
        body, _, radius, _, _layout = preview_buffer(mesh)
        packed = np.frombuffer(body, dtype=np.float32).reshape(2, 3, 18)
        self.assertTrue(np.isfinite(packed).all())
        np.testing.assert_array_equal(packed[0, :, 3:6], np.zeros((3, 3)))
        self.assertEqual(radius, 1)

    def test_automatic_tip_centres_lowest_bounds_without_vertex_density_bias(self):
        triangle = ((-2, -4, 0), (6, -4, 0), (-2, 8, 0))
        uneven = mesh_from_arrays([triangle, triangle, triangle,
                                  ((-2, -4, 0), (-2, -3, 1), (-1, -4, 1))])
        self.assertEqual(uneven.automatic_tip, (2, 2, 0))
        self.assertEqual(mesh_from_arrays([triangle]).automatic_tip, uneven.automatic_tip)

    def test_automatic_tip_ignores_high_body_and_includes_near_bottom_tolerance(self):
        mesh = mesh_from_arrays([((2, 3, -5), (4, 7, -4.9995), (200, 200, 50)),
                                 ((-100, -100, 50), (100, -100, 50), (0, 100, 50))])
        self.assertEqual(mesh.automatic_tip, (3, 5, -5))

    def test_arrays_are_detached_and_read_only_with_exact_black_preserved(self):
        source = np.array([((0, 0, 0), (1, 0, 0), (0, 1, 0))], dtype=np.float32)
        colours = np.array([(0, 0, 0, 1)], dtype=np.float32)
        mesh = mesh_from_arrays(source, colours)
        source[:] = 99
        colours[:] = .5
        self.assertEqual(mesh.triangles[0, 0].tolist(), [0, 0, 0])
        self.assertEqual(mesh.colours[0].tolist(), [0, 0, 0, 1])
        with self.assertRaises(ValueError):
            mesh.triangles[0, 0, 0] = 1
        with self.assertRaises(ValueError):
            mesh.colours[0, 0] = 1

    def test_invalid_geometry_is_rejected_before_it_can_be_rendered(self):
        inputs = (
            [],
            [((0, 0, 0), (0, 0, 0), (0, 0, 0))],
            [((0, 0, float("nan")), (1, 0, 0), (0, 1, 0))],
            [((0, 0, 0), (float("inf"), 0, 0), (0, 1, 0))],
            [((10001, 0, 0), (10002, 0, 0), (10001, 1, 0))],
            [((0, 0, 0), (2001, 0, 0), (0, 1, 0))],
        )
        for values in inputs:
            with self.subTest(values=values), self.assertRaises(ValueError):
                mesh_from_arrays(values)
        triangles = default_mesh().triangles[:2]
        with patch("mpf.geometry.ToolheadGeometry.MAX_TRIANGLES", 1):
            with self.assertRaises(ValueError):
                mesh_from_arrays(triangles)

    def test_bad_colour_shape_and_non_finite_colours_are_rejected(self):
        triangles = default_mesh().triangles[:2]
        for colours in ([(1, 0, 0, 1)], [(float("nan"), 0, 0, 1)] * 2):
            with self.subTest(colours=colours), self.assertRaises(ValueError):
                mesh_from_arrays(triangles, colours)

    def test_xyz_override_requires_three_finite_bounded_values(self):
        self.assertEqual(valid_tip(["-2.5", "0", "10"]), (-2.5, 0, 10))
        self.assertEqual(valid_tip([-10000, 10000, 0]), (-10000, 10000, 0))
        for value in (None, [], [0, 0], [0, 0, 0, 0], [0, "", 0],
                      [0, "nan", 0], [0, "inf", 0], [10001, 0, 0]):
            with self.subTest(value=value):
                self.assertIsNone(valid_tip(value))

    def test_default_indicator_has_a_nozzle_at_the_origin(self):
        mesh = default_mesh()
        self.assertEqual(mesh.automatic_tip, (0, 0, 0))
        self.assertGreater(float(mesh.triangles[:, :, 2].max()), 0)

    def test_surface_pick_roundtrips_after_orbit_resize_and_zoom(self):
        triangle = np.array(((1, 2, 3), (7, 2, 3), (1, 8, 3)), dtype=np.float32)
        mesh = mesh_from_arrays([triangle])
        expected = .2 * triangle[0] + .3 * triangle[1] + .5 * triangle[2]
        for yaw, pitch, width, height, zoom in ((0, 45, 400, 250, 1),
                                               (37, 25, 640, 240, 2),
                                               (-80, -40, 320, 320, .5)):
            with self.subTest(yaw=yaw, pitch=pitch, zoom=zoom):
                projected, _, _, _ = camera_projection(mesh, yaw, pitch, width, height, zoom)
                point = .2 * projected[0, 0] + .3 * projected[0, 1] + .5 * projected[0, 2]
                picked = pick_projected(mesh, projected, *point[:2])
                np.testing.assert_allclose(picked, expected, atol=1e-5)
                self.assertIsNone(pick_projected(mesh, projected, -10000, -10000))

    def test_surface_pick_selects_frontmost_overlapping_triangle(self):
        mesh = mesh_from_arrays([((0, -2, 0), (10, -2, 0), (0, -2, 10)),
                                 ((0, 2, 0), (10, 2, 0), (0, 2, 10))])
        projected, _, _, _ = camera_projection(mesh, 0, 0, 400, 250)
        point = projected[0].mean(axis=0)
        picked = pick_projected(mesh, projected, *point[:2])
        np.testing.assert_allclose(picked, (10 / 3, -2, 10 / 3), atol=1e-5)

    def test_edge_on_surfaces_are_not_pickable(self):
        mesh = mesh_from_arrays([((0, 0, 0), (10, 0, 0), (0, 10, 0))])
        projected, _, _, _ = camera_projection(mesh, 0, 0, 400, 250)
        self.assertIsNone(pick_projected(mesh, projected, 200, 125))

    def test_visible_triangle_order_culls_offscreen_and_fully_transparent_faces(self):
        projected = np.array([
            ((1, 1, 3), (8, 1, 3), (1, 8, 3)),
            ((1, 1, 1), (8, 1, 1), (1, 8, 1)),
            ((20, 1, 2), (28, 1, 2), (20, 8, 2)),
            ((1, 20, 2), (8, 20, 2), (1, 28, 2)),
            ((1, 1, 4), (8, 1, 4), (1, 8, 4)),
            ((1, 1, 5), (1.01, 1, 5), (1, 1.01, 5)),
        ], dtype=np.float32)
        colours = np.tile((0, 0, 0, 1), (len(projected), 1))
        colours[4, 3] = 0
        np.testing.assert_array_equal(visible_triangles(projected, 10, 10, colours), [1, 0])
        np.testing.assert_array_equal(visible_triangles(projected), [1, 2, 3, 0, 4])


class ToolheadAssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ToolheadAssetStore(self.temporary.name)

    def test_content_addressed_restore_retains_colours_and_anchor_without_cad(self):
        mesh = mesh_from_arrays([((2, 4, -1), (6, 4, -1), (2, 8, 5))], [(0, .5, 1, .75)])
        key = self.store.publish(mesh)
        self.assertEqual(len(key), 64)
        restored = ToolheadAssetStore(self.temporary.name).load(key)
        np.testing.assert_array_equal(restored.triangles, mesh.triangles)
        np.testing.assert_array_equal(restored.colours, mesh.colours)
        self.assertEqual(restored.automatic_tip, mesh.automatic_tip)
        self.assertEqual(self.store.publish(restored), key)
        self.assertEqual(os.listdir(self.temporary.name), [key + ".mesh"])

    def test_invalid_keys_never_select_files_outside_the_store(self):
        for key in ("../outside", "A" * 64, "0" * 63, "0" * 65, None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.load(key)

    def test_corrupt_or_truncated_content_is_not_accepted(self):
        key = self.store.publish(default_mesh())
        filename = os.path.join(self.temporary.name, key + ".mesh")
        with open(filename, "rb") as source:
            original = source.read()
        variants = (original[:5], original[:-1], original + b"extra",
                    original[:HEADER.size] + bytes([original[HEADER.size] ^ 1]) + original[HEADER.size+1:])
        for body in variants:
            with self.subTest(length=len(body)):
                with open(filename, "wb") as target:
                    target.write(body)
                with self.assertRaises(ValueError):
                    self.store.load(key)

    def test_bad_format_and_count_are_rejected_without_large_allocation(self):
        for magic, count in ((b"OLDHEAD0", 1), (MAGIC, 0), (MAGIC, MAX_TRIANGLES + 1)):
            body = HEADER.pack(magic, count)
            key = hashlib.sha256(body).hexdigest()
            with open(os.path.join(self.temporary.name, key + ".mesh"), "wb") as target:
                target.write(body)
            with self.subTest(magic=magic, count=count), self.assertRaises(ValueError):
                self.store.load(key)

    def test_failed_publication_preserves_previous_asset_and_removes_temporary(self):
        previous = self.store.publish(default_mesh())
        mesh = mesh_from_arrays([((1, 2, 3), (4, 2, 3), (1, 5, 3))])
        with patch("mpf.toolhead.ToolheadAssetStore.os.replace", side_effect=OSError("disk refused")):
            with self.assertRaisesRegex(OSError, "disk refused"):
                self.store.publish(mesh)
        self.assertEqual(os.listdir(self.temporary.name), [previous + ".mesh"])
        self.assertEqual(self.store.load(previous).automatic_tip, (0, 0, 0))


class ToolheadStlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.filename = os.path.join(self.temporary.name, "head.stl")

    def write(self, body):
        with open(self.filename, "wb") as target:
            target.write(body)

    def test_binary_solid_header_is_read_as_binary_with_source_coordinates(self):
        triangle = ((1, 2, -3), (4, 2, -3), (1, 5, 7))
        self.write(binary_stl([triangle]))
        mesh = read_stl(self.filename)
        np.testing.assert_array_equal(mesh.triangles, [triangle])
        self.assertEqual(mesh.automatic_tip, (2.5, 2, -3))

    def test_ascii_scientific_notation_preserves_millimetre_coordinates(self):
        self.write(b"solid head\nfacet normal 0 0 1\nouter loop\n"
                   b"vertex -2e0 0 0\nvertex 2.5E+0 0 0\nvertex 0 .5 1e1\n"
                   b"endloop\nendfacet\nendsolid head\n")
        mesh = read_stl(self.filename)
        np.testing.assert_allclose(mesh.triangles, [[(-2, 0, 0), (2.5, 0, 0), (0, .5, 10)]])

    def test_malformed_binary_and_ascii_models_are_refused(self):
        binary = binary_stl([((0, 0, 0), (1, 0, 0), (0, 1, 0))])
        bodies = (binary[:-1], binary + b"trailing", b"not a model",
                  b"solid head\nvertex 0 0 0\nendfacet\nendsolid head",
                  b"solid head\nendsolid head", binary_stl([((0, 0, 0),) * 3]))
        for body in bodies:
            with self.subTest(length=len(body)):
                self.write(body)
                with self.assertRaises(ValueError):
                    read_stl(self.filename)

    def test_input_byte_and_triangle_caps_are_enforced(self):
        self.write(binary_stl([((0, 0, 0), (1, 0, 0), (0, 1, 0))] * 2))
        with patch("mpf.toolhead.ToolheadImport.MAX_FILE_BYTES", 100):
            with self.assertRaises(ValueError):
                read_stl(self.filename)
        with patch("mpf.toolhead.ToolheadImport.MAX_TRIANGLES", 1):
            with self.assertRaises(ValueError):
                read_stl(self.filename)

    def test_pre_cancelled_stl_read_never_publishes_geometry(self):
        self.write(binary_stl([((0, 0, 0), (1, 0, 0), (0, 1, 0))]))
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ValueError, "cancel"):
            read_stl(self.filename, cancelled)
