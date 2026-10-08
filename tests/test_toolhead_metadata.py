"""Durable material/body identity and bounded untrusted CAD metadata."""
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import numpy as np

from mpf.geometry.ToolheadGeometry import default_mesh, mesh_from_arrays, mesh_from_payload, mesh_metadata
from mpf.geometry.ToolheadMeshFormat import (
    HEADER, MAGIC, MAX_METADATA_BYTES, UNKNOWN_METADATA, decode_metadata,
    display_name, encode_metadata, read_payload, validate_metadata,
)
from mpf.toolhead.ToolheadAssetStore import ToolheadAssetStore


def annotated_mesh():
    metadata = {
        "materials": [{"name": "ABS", "description": "Printed housing", "source": "step-material"},
                      {"name": "Copper", "description": "Nozzle", "source": "step-material"}],
        "bodies": [{"name": "Rotor", "source": "step-name", "centre": [1, 2, 3], "axis": [0, 0, 1]},
                   {"name": "Rotor", "source": "step-name", "centre": [10, 20, 30], "axis": [1, 0, 0]}],
    }
    return mesh_from_arrays(default_mesh().triangles[:2], [(0, 0, 0, 1), (.5, .2, 0, .35)], [7, 8],
                            material_ids=[0, 1], body_ids=[0, 1], metadata=metadata)


class ToolheadMetadataTests(unittest.TestCase):
    def test_distinct_repeated_bodies_material_and_alpha_survive_content_addressed_restore(self):
        mesh = annotated_mesh()
        with tempfile.TemporaryDirectory() as root:
            store = ToolheadAssetStore(root)
            key = store.publish(mesh)
            restored = store.load(key)
            np.testing.assert_array_equal(restored.body_ids, [0, 1])
            np.testing.assert_array_equal(restored.material_ids, [0, 1])
            np.testing.assert_array_equal(restored.colours, mesh.colours)
            self.assertEqual(restored.materials, mesh.materials)
            self.assertEqual(restored.bodies, mesh.bodies)
            self.assertNotEqual(restored.bodies[0].centre, restored.bodies[1].centre)
            self.assertEqual(store.publish(restored), key)
            for identifiers in (restored.body_ids, restored.material_ids):
                with self.assertRaises(ValueError): identifiers[0] = 99
            with self.assertRaises(FrozenInstanceError): restored.materials[0].name = "Steel"

    def test_old_meshes_keep_geometry_colours_and_faces_without_invented_body_metadata(self):
        mesh = default_mesh()
        count = len(mesh.triangles)
        with tempfile.TemporaryDirectory() as root:
            for version in (1, 2):
                header = HEADER.pack(f"MPFHEAD{version}".encode(), count)
                payload = mesh.triangles.astype("<f4").tobytes() + mesh.colours.astype("<f4").tobytes()
                if version == 2: payload += mesh.surfaces.astype("<u4").tobytes()
                key = hashlib.sha256(header + payload).hexdigest()
                Path(root, key + ".mesh").write_bytes(header + payload)
                restored = ToolheadAssetStore(root).load(key)
                np.testing.assert_array_equal(restored.triangles, mesh.triangles)
                np.testing.assert_array_equal(restored.colours, mesh.colours)
                np.testing.assert_array_equal(restored.surfaces, mesh.surfaces)
                self.assertEqual(mesh_metadata(restored), UNKNOWN_METADATA)
                np.testing.assert_array_equal(restored.body_ids, np.zeros(count))

    def test_metadata_is_part_of_asset_hash_and_stays_detached(self):
        original = annotated_mesh()
        metadata = mesh_metadata(original)
        changed = mesh_from_arrays(original.triangles, original.colours, original.surfaces,
            material_ids=original.material_ids, body_ids=original.body_ids, metadata=metadata)
        metadata["materials"][0]["name"] = "Steel"
        self.assertEqual(changed.materials[0].name, "ABS")
        with tempfile.TemporaryDirectory() as root:
            store = ToolheadAssetStore(root)
            key = store.publish(original)
            data = Path(root, key + ".mesh").read_bytes().replace(b"ABS", b"ASA")
            Path(root, key + ".mesh").write_bytes(data)
            with self.assertRaisesRegex(ValueError, "damaged"): store.load(key)

    def test_bad_identifiers_cannot_reference_unrelated_materials_or_bodies(self):
        mesh = annotated_mesh()
        for ids in ([0], [-1, 0], [0, 2], [0, .5], [0, float("nan")]):
            for key in ("material_ids", "body_ids"):
                with self.subTest(key=key, ids=ids), self.assertRaises(ValueError):
                    mesh_from_arrays(mesh.triangles, metadata=mesh_metadata(mesh), **{key: ids})

    def test_invalid_tables_text_and_axes_are_rejected(self):
        malformed = [None, [], {}, {"materials": [], "bodies": []}]
        for key, entry, field, value in (
                ("materials", 0, "name", "x" * 161), ("materials", 0, "name", "hello\nworld"),
                ("materials", 0, "description", None), ("materials", 0, "source", "rgb-guess"),
                ("bodies", 0, "axis", [0, 0, 0]), ("bodies", 0, "axis", [True, 0, 0]),
                ("bodies", 0, "axis", [float("nan"), 0, 0]), ("bodies", 0, "axis", [1, 0]),
                ("bodies", 0, "centre", [10001, 0, 0]), ("bodies", 0, "centre", [10**400, 0, 0]), ("bodies", 0, "centre", None)):
            metadata = mesh_metadata(annotated_mesh())
            metadata[key][entry][field] = value
            malformed.append(metadata)
        for metadata in malformed:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError): validate_metadata(metadata)
        metadata = copy.deepcopy(UNKNOWN_METADATA)
        metadata["materials"] *= 4097
        with self.assertRaises(ValueError): encode_metadata(metadata)
        metadata = copy.deepcopy(UNKNOWN_METADATA)
        metadata["bodies"][0]["extra"] = 1
        with self.assertRaises(ValueError): encode_metadata(metadata)

    def test_encoding_rejects_duplicate_keys_invalid_utf8_deep_json_and_byte_overflow(self):
        for data in (b'{"materials":[],"materials":[],"bodies":[]}', b'\xff', b'{', b'[' * 2000):
            with self.subTest(data=data[:30]), self.assertRaises(ValueError): decode_metadata(data)
        with self.assertRaises(ValueError): decode_metadata(b" " * (MAX_METADATA_BYTES + 1))
        with patch("mpf.geometry.ToolheadMeshFormat.MAX_METADATA_BYTES", 4):
            with self.assertRaises(ValueError): encode_metadata(UNKNOWN_METADATA)
        self.assertEqual(decode_metadata(encode_metadata(UNKNOWN_METADATA)), UNKNOWN_METADATA)
        self.assertEqual(display_name("\n<part>\x00", "Unknown"), "<part>")
        self.assertEqual(display_name("\n", "Unknown"), "Unknown")

    def test_payload_lengths_are_checked_before_large_metadata_reads(self):
        plane = bytes(64)
        metadata = encode_metadata(UNKNOWN_METADATA)
        prefix = HEADER.pack(MAGIC, 1) + plane
        cases = (b"short", HEADER.pack(b"INVALID0", 1), HEADER.pack(MAGIC, 1) + plane[:-1],
                 prefix, prefix + struct.pack("<I", 0), prefix + struct.pack("<I", MAX_METADATA_BYTES + 1),
                 prefix + struct.pack("<I", len(metadata)) + metadata[:-1],
                 prefix + struct.pack("<I", len(metadata)) + metadata + b"extra")
        for payload in cases:
            with self.subTest(length=len(payload)), self.assertRaises(ValueError): read_payload(io.BytesIO(payload))

    def test_payload_rejects_valid_json_with_out_of_table_triangle_ids(self):
        mesh = annotated_mesh()
        count = len(mesh.triangles)
        encoded = encode_metadata(mesh_metadata(mesh))
        plane = (mesh.triangles.tobytes() + mesh.colours.tobytes() + mesh.surfaces.tobytes()
                 + np.array([0, 99], dtype="<u4").tobytes() + mesh.body_ids.tobytes())
        payload = HEADER.pack(MAGIC, count) + plane + struct.pack("<I", len(encoded)) + encoded
        _, body, count, stride, metadata = read_payload(io.BytesIO(payload))
        with self.assertRaises(ValueError): mesh_from_payload(body, count, stride, metadata)
        malformed = json.loads(encoded)
        malformed["bodies"][0]["axis"] = [1, 0, "bad"]
        with self.assertRaises(ValueError): decode_metadata(json.dumps(malformed).encode())


if __name__ == "__main__": unittest.main()
