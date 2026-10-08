"""Bounded, hash-covered CAD metadata shared with the isolated STEP worker.

This module uses only the standard library; the disposable CAD interpreter
loads it without importing the plugin, Qt or NumPy.
"""
from __future__ import annotations

import json
import math
import struct

HEADER = struct.Struct("<8sI")
MAGIC = b"MPFHEAD3"
MAX_METADATA_BYTES = 1024 * 1024
MAX_PARTS = 4096
MAX_NAME = 160
UNKNOWN_METADATA = {
    "materials": [{"name": "Unknown material", "description": "", "source": "unknown"}],
    "bodies": [{"name": "Whole model", "source": "unknown", "centre": None, "axis": None}],
}


def display_name(value, fallback):
    """Imported text is plain display data, never markup or an identifier."""
    return "".join(character for character in str(value) if character.isprintable())[:MAX_NAME].strip() or fallback


def _name(value):
    if not isinstance(value, str) or len(value) > MAX_NAME or any(not c.isprintable() for c in value):
        raise ValueError("Invalid CAD metadata text")
    return value


def validate_metadata(value):
    if not isinstance(value, dict) or set(value) != {"materials", "bodies"}:
        raise ValueError("Invalid CAD metadata tables")
    for key in ("materials", "bodies"):
        table = value[key]
        if not isinstance(table, list) or not 0 < len(table) <= MAX_PARTS:
            raise ValueError("CAD metadata exceeds its part limit")
        for item in table:
            keys = {"name", "description", "source"} if key == "materials" else {"name", "source", "centre", "axis"}
            if not isinstance(item, dict) or set(item) != keys:
                raise ValueError("Invalid CAD metadata entry")
            _name(item["name"])
            if item["source"] not in ("unknown", "step-material", "step-name"):
                raise ValueError("Invalid CAD metadata provenance")
            if key == "materials":
                _name(item["description"])
                continue
            centre, axis = item["centre"], item["axis"]
            if (centre is None) != (axis is None):
                raise ValueError("Incomplete CAD rotation axis")
            if centre is not None:
                for vector in (centre, axis):
                    if not isinstance(vector, list) or len(vector) != 3 or any(
                            isinstance(number, bool) or not isinstance(number, (int, float))
                            or abs(number) > 10_000 or not math.isfinite(number) for number in vector):
                        raise ValueError("Invalid CAD rotation axis")
                if not .999 <= sum(number * number for number in axis) <= 1.001:
                    raise ValueError("CAD rotation axis must be normalized")
    return value


def encode_metadata(value):
    body = json.dumps(validate_metadata(value), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(body) > MAX_METADATA_BYTES:
        raise ValueError("CAD metadata exceeds its byte limit")
    return body


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("Duplicate CAD metadata key")
        result[key] = value
    return result


def decode_metadata(body):
    if len(body) > MAX_METADATA_BYTES: raise ValueError("CAD metadata exceeds its byte limit")
    try:
        value = json.loads(body, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Invalid CAD metadata encoding") from error
    return validate_metadata(value)


def read_payload(source, max_triangles=1_000_000):
    """Check lengths before allocating; reject trailing bytes in every version."""
    header = source.read(HEADER.size)
    if len(header) != HEADER.size: raise ValueError("Truncated toolhead mesh")
    magic, count = HEADER.unpack(header)
    if magic not in (b"MPFHEAD1", b"MPFHEAD2", MAGIC) or not 0 < count <= max_triangles:
        raise ValueError("Invalid toolhead mesh format")
    stride = {b"MPFHEAD1": 52, b"MPFHEAD2": 56, MAGIC: 64}[magic]
    body = source.read(count * stride)
    if len(body) != count * stride: raise ValueError("Invalid toolhead mesh length")
    metadata = UNKNOWN_METADATA
    if magic == MAGIC:
        length = source.read(4)
        if len(length) != 4: raise ValueError("Truncated CAD metadata length")
        size, = struct.unpack("<I", length)
        if not 0 < size <= MAX_METADATA_BYTES: raise ValueError("Invalid CAD metadata length")
        encoded = source.read(size)
        if len(encoded) != size: raise ValueError("Truncated CAD metadata")
        metadata = decode_metadata(encoded)
        body += length + encoded
    if source.read(1): raise ValueError("Invalid toolhead mesh length")
    return header, body, count, stride, metadata
