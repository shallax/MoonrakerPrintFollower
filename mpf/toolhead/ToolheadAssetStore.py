"""Content-addressed immutable meshes; configuration adoption is the commit point."""
from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
import numpy as np
from ..geometry.ToolheadGeometry import MAX_TRIANGLES, mesh_from_arrays

HEADER = struct.Struct("<8sI")
MAGIC = b"MPFHEAD2"


class ToolheadAssetStore:
    def __init__(self, root):
        self.root = root

    def publish(self, mesh):
        body = HEADER.pack(MAGIC, len(mesh.triangles)) + mesh.triangles.astype("<f4").tobytes() + mesh.colours.astype("<f4").tobytes() + mesh.surfaces.astype("<u4").tobytes()
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
            header = source.read(HEADER.size)
            if len(header) != HEADER.size: raise ValueError("Truncated toolhead model")
            magic, count = HEADER.unpack(header)
            if magic not in (b"MPFHEAD1", MAGIC) or not 0 < count <= MAX_TRIANGLES: raise ValueError("Invalid toolhead mesh format")
            stride = 56 if magic == MAGIC else 52
            body = source.read(count * stride + 1)
        if len(body) != count * stride or hashlib.sha256(header + body).hexdigest() != key:
            raise ValueError("Stored toolhead model is damaged")
        values = np.frombuffer(body, dtype="<f4")
        return mesh_from_arrays(values[:count*9].reshape(count, 3, 3), values[count*9:count*13].reshape(count, 4),
            np.frombuffer(body, dtype="<u4", offset=count*52) if stride == 56 else None)
