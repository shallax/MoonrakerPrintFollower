"""Avoid repeated Qt uniform uploads in private toolhead shader programs."""
import struct


def delivered_key(value):
    """Snapshot the host overload without retaining mutable caller values."""
    kind = type(value)
    if kind is bool: return 'bool', value
    if kind is int: return 'int', value
    if kind is float: return 'float', struct.pack('!d', value)
    if kind is list:
        if len(value) in (2, 3, 4) and all(type(v) in (int, float) for v in value):
            return 'vector-list', tuple(map(type, value)), struct.pack('!' + 'd'*len(value), *value)
        # Nested vec2 lists use an ARRAY overload in Uranium's scalar setter.
        return None
    from UM.Math.Matrix import Matrix
    from UM.Math.Vector import Vector
    from UM.Math.Color import Color
    if kind is Matrix:
        data = value.getData()
        return 'matrix', data.shape, data.dtype.str, data.tobytes()
    if kind is Vector:
        return 'vector3', struct.pack('!ddd', value.x, value.y, value.z)
    if kind is Color:
        return 'colour', struct.pack('!dddd', value.r, value.g, value.b, value.a)
    return None


class DeliveredUniformCache:
    def _delivery_cache(self):
        program = getattr(self, '_shader_program', None)
        if getattr(self, '_delivered_program', None) is not program:
            self._delivered_uniforms = {}
            self._delivered_program = program
        if not hasattr(self, '_delivered_uniforms'):
            self._delivered_uniforms = {}
        return self._delivered_uniforms

    def build(self):
        cache = self._delivery_cache()
        cache.clear()
        try:
            return super().build()
        finally:
            cache.clear()

    def _setUniformValueDirect(self, uniform, value):
        cache = self._delivery_cache()
        try:
            key = delivered_key(value)
        except Exception:
            key = None  # Unknown conversion follows the original host behavior.
        if key is None:
            cache.clear()
            try:
                return super()._setUniformValueDirect(uniform, value)
            finally:
                cache.clear()
        if uniform in cache and cache[uniform] == key:
            return
        cache.pop(uniform, None)
        try:
            result = super()._setUniformValueDirect(uniform, value)
        except Exception:
            cache.clear()
            raise
        cache[uniform] = key
        return result

    def _setUniformValueArrayDirect(self, uniform, value):
        cache = self._delivery_cache()
        cache.clear()
        try:
            return super()._setUniformValueArrayDirect(uniform, value)
        finally:
            cache.clear()
