"""Plain immutable capture values crossing the reflection context boundary."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class UniformValue:
    kind: str
    value: object


def freeze_uniform(value):
    """Retain native Color's byte quantization and distinguish vectors/matrices."""
    from UM.Math.Matrix import Matrix
    from UM.Math.Vector import Vector
    from UM.Math.Color import Color
    if type(value) is Matrix:
        return UniformValue('matrix', tuple(map(float, np.asarray(value.getData()).reshape(-1))))
    if type(value) is Vector: return UniformValue('vector', (float(value.x), float(value.y), float(value.z)))
    if type(value) is Color: return UniformValue('colour', (float(value.r), float(value.g), float(value.b), float(value.a)))
    if isinstance(value, (list, tuple)):
        return UniformValue('list', tuple(freeze_uniform(part) for part in value))
    if type(value) in (bool, int, float): return UniformValue('scalar', value)
    if isinstance(value, np.integer): return UniformValue('scalar', int(value))
    if isinstance(value, np.floating): return UniformValue('scalar', float(value))
    raise RuntimeError('Unsupported reflection uniform value')


def thaw_uniform(value):
    if value.kind == 'scalar': return value.value
    if value.kind == 'list': return [thaw_uniform(part) for part in value.value]
    if value.kind == 'matrix':
        from UM.Math.Matrix import Matrix
        return Matrix(np.asarray(value.value).reshape(4, 4))
    if value.kind == 'vector':
        from UM.Math.Vector import Vector
        return Vector(*value.value)
    if value.kind == 'colour':
        from UM.Math.Color import Color
        return Color(*value.value)
    raise RuntimeError('Unknown reflection uniform value')


def frozen_array(value):
    if value is None: return None
    array = np.asarray(value).view()
    if array.dtype.hasobject: raise RuntimeError('Reflection arrays cannot contain live objects')
    array.flags.writeable = False  # Never change the host array's write flag.
    return array


@dataclass(frozen=True, eq=False)
class CaptureMesh:
    """Published CPU arrays only; no native QObject/cache fields are retained."""
    identity: int
    vertices: object
    indices: object
    normals: object
    colours: object
    uvs: object
    attributes: tuple

    @classmethod
    def freeze(cls, mesh, *, identity=None):
        indices = mesh.getIndices()
        colours = None
        if mesh.hasColors():
            source = getattr(mesh, 'source', mesh)
            published = getattr(source, '_colors', None)
            if isinstance(published, np.ndarray) and published.dtype == np.float32 and published.shape == (mesh.getVertexCount(), 4):
                colours = published
            else:
                if mesh.getVertexCount() > 250000: raise RuntimeError('Reflection colour publication is unsupported')
                colours = np.frombuffer(mesh.getColorsAsByteArray(), dtype=np.float32).reshape(-1, 4)
        return cls(id(mesh) if identity is None else identity, frozen_array(mesh.getVertices()),
            frozen_array(indices) if indices is not None and len(indices) else None,
            frozen_array(mesh.getNormals()) if mesh.hasNormals() else None, frozen_array(colours),
            frozen_array(mesh.getUVCoordinates()) if mesh.hasUVCoordinates() else None,
            tuple((name, mesh.getAttribute(name)['opengl_name'],
                mesh.getAttribute(name)['opengl_type'], frozen_array(mesh.getAttribute(name)['value']))
                for name in mesh.attributeNames()))

    def getVertices(self): return self.vertices
    def getIndices(self): return self.indices
    def getNormals(self): return self.normals
    def getUVCoordinates(self): return self.uvs
    def getVertexCount(self): return len(self.vertices)
    def hasNormals(self): return self.normals is not None
    def hasColors(self): return self.colours is not None
    def hasUVCoordinates(self): return self.uvs is not None
    def attributeNames(self): return [name for name, *_rest in self.attributes]
    def getAttribute(self, name):
        for key, gl_name, kind, array in self.attributes:
            if name == key: return dict(opengl_name=gl_name, opengl_type=kind, value=array)
        return None

    def layout(self):
        offset, count = 0, self.getVertexCount()
        sizes = {'float': 1, 'int': 1, 'vector2f': 2, 'vector3f': 3, 'vector4f': 4}
        result = []
        for name, kind, data in [('a_vertex', 'vector3f', self.vertices), ('a_normal', 'vector3f', self.normals),
                ('a_color', 'vector4f', self.colours), ('a_uvs', 'vector2f', self.uvs),
                *((gl_name, kind, array) for _name, gl_name, kind, array in self.attributes)]:
            if data is None: continue
            size = sizes.get(kind)
            if size is None or np.asarray(data).size != count * size:
                raise RuntimeError(f'Reflection vertex layout is unsupported: {name} {kind} '
                    f'{np.asarray(data).shape} for {count} vertices')
            result.append((name, kind, offset))
            offset += count * size * 4
        return tuple(result), offset

    def upload_parts(self):
        return [value for value in (self.vertices, self.normals, self.colours, self.uvs,
            *(array for _name, _gl_name, _kind, array in self.attributes)) if value is not None]


@dataclass(frozen=True)
class BufferLease:
    name: int
    size: int
    layout: tuple

    def validate(self, mesh):
        layout, size = mesh.layout()
        if self.name <= 0 or self.size != size or self.layout != layout:
            raise RuntimeError('Reflection borrowed vertex buffer is incomplete')
