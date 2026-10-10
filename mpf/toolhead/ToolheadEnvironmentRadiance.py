"""Plain frozen native path appearance for one retained capture cohort.

This adapter delivers the actual captured uniforms and shader defaults. It
neither discovers a scene nor binds graphics objects. A newer live view/light
must never repaint geometry belonging to an older completed map.
"""
from dataclasses import dataclass
import math
import struct

from .ToolheadCaptureValues import UniformValue
from .ToolheadEnvironmentPaths import PathInputs, PathPrefix, certify_path_light


@dataclass(frozen=True)
class PathRadiance:
    uniforms: tuple
    key: tuple


def _number(value):
    if (type(value) not in (int, float, bool) or type(value) is int and value.bit_length() > 64
            or not math.isfinite(value)):
        raise ValueError('Finite plain captured number required')
    try: result = struct.unpack('f', struct.pack('f', value))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError('Captured value exceeds FLOAT32 storage') from error
    if not math.isfinite(result): raise ValueError('Captured value exceeds FLOAT32 storage')
    return result


def _plain(value, depth=0):
    if type(value) is not UniformValue or depth > 4:
        raise ValueError('Bounded frozen capture uniform required')
    if value.kind == 'scalar': return _number(value.value)
    if value.kind == 'list':
        if type(value.value) is not tuple or len(value.value) > 16:
            raise ValueError('Bounded frozen capture list required')
        return tuple(_plain(item, depth+1) for item in value.value)
    widths = {'matrix': 16, 'vector': 3, 'colour': 4}
    if value.kind not in widths or type(value.value) is not tuple or len(value.value) != widths[value.kind]:
        raise ValueError('Supported frozen capture uniform required')
    return tuple(_number(item) for item in value.value)


def _values(entries):
    if type(entries) is not tuple or len(entries) > 128:
        raise ValueError('Bounded frozen uniform delivery required')
    result = {}
    for entry in entries:
        if (type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not str
                or not entry[0] or len(entry[0]) > 128 or entry[0] in result):
            raise ValueError('Unique captured uniform names required')
        result[entry[0]] = _plain(entry[1])
    return result


def _vector(value, width):
    if type(value) is not tuple or len(value) != width or any(type(item) is tuple for item in value):
        raise ValueError('Captured vector shape differs from native shader')
    return tuple(_number(item) for item in value)


def _integer(value, maximum):
    # Validate BEFORE F32 conversion: native scalar float uploads do not
    # become integer uniforms merely because they round onto an integer.
    if (type(value) is not UniformValue or value.kind != 'scalar'
            or type(value.value) not in (int, bool) or not 0 <= value.value <= maximum):
        raise ValueError('Captured shader selector is invalid')
    return int(value.value)


def _freeze(value):
    if type(value) is tuple: return UniformValue('list', tuple(_freeze(item) for item in value))
    return UniformValue('scalar', value)


def freeze_path_radiance(inputs, prefix, *, model, uniforms, defaults, lighting,
                         light_effects, origin, camera_light, top_element, cancel=None):
    """Deliver ONLY this publication's values, including absent/off lighting.

    ``defaults`` is the real original path shader recipe's default list. Never
    invent palette/ambient values, flatten an extruder matrix as a vector, or
    substitute current node properties for the captured frame.
    """
    if type(inputs) is not PathInputs or type(prefix) is not PathPrefix:
        raise ValueError('Certified path publication required')
    if type(model) is not UniformValue or model.kind != 'matrix':
        raise ValueError('Frozen native model matrix required')
    matrix = _plain(model)
    if matrix[12:] != (0., 0., 0., 1.): raise ValueError('Native affine model required')
    if (type(light_effects) is not tuple or len(light_effects) != 2
            or any(type(value) is not bool for value in light_effects)):
        raise ValueError('Frozen capture lighting effects required')
    native = _values(defaults); native.update(_values(uniforms))
    native_kinds = dict(defaults); native_kinds.update(dict(uniforms))
    attached = _values(lighting); attached_kinds = dict(lighting)
    result = {}
    flags = (('Travel', 'travel_moves'), ('Helpers', 'helpers'), ('Skin', 'skin'),
             ('Infill', 'infill'), ('Starts', 'starts'))
    for output, source in flags:
        result['mpf_path_show'+output] = _integer(native_kinds['u_show_'+source], 1)
    result['mpf_pathView'] = _integer(native_kinds['u_layer_view_type'], 5)
    visibility = native['u_extruder_opacity']
    visibility_kind = native_kinds['u_extruder_opacity']
    # The native matrix expression uses COLUMN indexing; preserve its Matrix
    # kind and row-major CPU value so the normal Qt uploader performs transpose.
    if (visibility_kind.kind == 'list' and len(visibility_kind.value) == 4
            and all(type(row) is UniformValue and row.kind == 'list' for row in visibility_kind.value)):
        rows = tuple(_vector(row, 4) for row in visibility)
        visibility = tuple(item for row in rows for item in row)
    elif visibility_kind.kind == 'matrix': visibility = _vector(visibility, 16)
    else: raise ValueError('Native visibility requires Matrix or nested four-row list')
    result['mpf_pathVisibility'] = UniformValue('matrix', visibility)
    result['mpf_pathModel'] = UniformValue('matrix', matrix)
    result['mpf_pathProbe'] = _vector(origin, 3)
    if type(camera_light) is not UniformValue or camera_light.kind != 'vector':
        raise ValueError('Frozen native camera light required')
    result['mpf_pathCameraLight'] = _vector(_plain(camera_light), 3)
    result['mpf_pathMinimumAlbedo'] = _vector(native['u_minimumAlbedo'], 4)[:3]
    result['mpf_pathStartsColour'] = _vector(native['u_starts_color'], 4)
    for output, source in (('Minimum', 'min'), ('Maximum', 'max')):
        result['mpf_path'+output] = tuple(native['u_'+source+'_'+metric]
            for metric in ('feedrate', 'thickness', 'line_width', 'flow_rate'))
        _vector(result['mpf_path'+output], 4)
    fields = {name: (offset, width) for name, offset, width in inputs.fields}
    for output, source in (('Position', 'a_vertex'), ('Dimensions', 'a_line_dim'), ('Type', 'a_line_type'),
            ('Previous', 'a_prev_line_type'), ('Extruder', 'a_extruder'), ('Colour', 'a_color'),
            ('MaterialColour', 'a_material_color'), ('Feedrate', 'a_feedrate')):
        offset, width = fields[source]
        result['mpf_path'+output+('Offset' if output in ('Colour', 'MaterialColour', 'Feedrate') else '')] = offset
        result['mpf_path'+output+'Stride'] = width
    count = _integer(attached_kinds['u_attachedCount'], 8) if attached else 0
    enabled = bool(attached) and light_effects[1] and count > 0
    certificate = certify_path_light(inputs, prefix, top_element, enabled=enabled,
        show_starts=bool(result['mpf_path_showStarts']), cancel=cancel)
    result.update(mpf_pathLightModels=int(enabled), mpf_pathLightCertified=1,
        mpf_pathCollectLight=int(enabled), mpf_pathLightTop=top_element//2,
        mpf_pathAttachedCount=count, mpf_pathLightOrthographic=0,
        mpf_pathLightViewDirection=(0., 0., 1.), mpf_pathLightOpacity=attached['u_lightOpacity'] if attached else 0.)
    for index in range(count):
        for kind in ('Position', 'Direction', 'Colour'):
            name = 'u_attached'+kind+'['+str(index)+']'
            result['mpf_pathAttached'+kind+'['+str(index)+']'] = _vector(attached[name], 3)
        reach = attached['u_attachedRange['+str(index)+']']
        if type(reach) is not float or reach < 0: raise ValueError('Captured light range is invalid')
        result['mpf_pathAttachedRange['+str(index)+']'] = reach
    if not 0 <= result['mpf_pathLightOpacity'] <= 1: raise ValueError('Captured light opacity is invalid')
    values = tuple((name, value if type(value) is UniformValue else _freeze(value)) for name, value in result.items())
    return PathRadiance(values, (certificate, values))
