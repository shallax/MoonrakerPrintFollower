"""Content-addressed immutable meshes; configuration adoption is the commit point."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from ..geometry.ToolheadGeometry import MAX_TRIANGLES, mesh_from_payload, mesh_metadata
from ..geometry.ToolheadMeshFormat import HEADER, MAGIC, encode_metadata, read_payload

class ToolheadAssetStore:
    def __init__(self, root):
        self.root = root

    def publish(self, mesh):
        import struct
        metadata = encode_metadata(mesh_metadata(mesh))
        body = (HEADER.pack(MAGIC, len(mesh.triangles)) + mesh.triangles.astype("<f4").tobytes()
                + mesh.colours.astype("<f4").tobytes() + mesh.surfaces.astype("<u4").tobytes()
                + mesh.material_ids.astype("<u4").tobytes() + mesh.body_ids.astype("<u4").tobytes()
                + struct.pack("<I", len(metadata)) + metadata)
        key = hashlib.sha256(body).hexdigest()
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, os.path.join(self.root, key + ".mesh"))
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return key

    def load(self, key):
        if not re.fullmatch(r"[0-9a-f]{64}", str(key)):
            raise ValueError("Invalid stored toolhead model")
        path = os.path.join(self.root, key + ".mesh")
        with open(path, "rb") as source:
            header, body, count, stride, metadata = read_payload(source, MAX_TRIANGLES)
        if hashlib.sha256(header + body).hexdigest() != key:
            raise ValueError("Stored toolhead model is damaged")
        return mesh_from_payload(body, count, stride, metadata)
