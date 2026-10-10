"""Frozen delivered point lights and complete point-shadow storage admission.

These values do not allocate maps or change shading. Shadow consumers must use
the same delivered source values as the existing direct-light shaders.
"""
from dataclasses import dataclass
from fractions import Fraction
import math
import struct

MAP_SIZE = 1024
MAX_MAP_BYTES = 512 * 1024 * 1024
MAX_LIGHTS = 2061  # Head4, attached8, path1 and up to2048 native plate recipes.


def vector3(value):
    result = tuple(float(component) for component in value)
    if len(result) != 3 or not all(math.isfinite(component) for component in result):
        raise ValueError('Shadow light vector is invalid')
    return result


def float32(value):
    try:
        result = struct.unpack('f', struct.pack('f', value))[0]
    except OverflowError as error:
        raise ValueError('Shadow value exceeds GPU float storage') from error
    if not math.isfinite(result):
        raise ValueError('Shadow value exceeds GPU float storage')
    return result


def certify_key(value):
    """Source/pose keys contain values, never mutable scene wrappers."""
    if type(value) is tuple:
        for item in value:
            certify_key(item)
    elif type(value) in (str, bytes, int, bool) or value is None:
        return
    elif type(value) is float and math.isfinite(value):
        return
    else:
        raise ValueError('Point-shadow source key is not immutable and finite')


@dataclass(frozen=True)
class ShadowLight:
    kind: str
    index: int
    position: tuple
    direction: tuple | None
    colour: tuple
    reach: float | None = None

    def __post_init__(self):
        limits = {'head': 4, 'attached': 8, 'path': 1, 'platform': 2048}
        if (self.kind not in limits or type(self.index) is not int or
                not 0 <= self.index < limits[self.kind]):
            raise ValueError('Shadow light consumer is invalid')
        for name in ('position', 'colour'):
            object.__setattr__(self, name, vector3(getattr(self, name)))
        if self.kind in ('head', 'attached'):
            object.__setattr__(self, 'direction', vector3(self.direction))
            if not any(self.direction):
                raise ValueError('Shadow light direction is invalid')
        elif self.direction is not None:
            raise ValueError('Native point light has no directed emission')
        if min(self.colour) < 0:
            raise ValueError('Shadow light energy or direction is invalid')
        if self.reach is not None:
            reach = float(self.reach)
            if not math.isfinite(reach) or reach <= 0:
                raise ValueError('Shadow light range is invalid')
            object.__setattr__(self, 'reach', reach)

    @property
    def active(self):
        return any(self.colour)

    def certify_gpu(self):
        # This is shadow admission ONLY. Existing ordinary light delivery is
        # preserved, even when a future optional shadow owner must refuse it.
        for value in self.position + self.colour:
            float32(value)
        if self.direction is not None:
            direction = tuple(float32(v) for v in self.direction)
            squared = float32(sum(v*v for v in direction))
            if squared < 1.1754943508222875e-38:
                raise ValueError('Shadow direction cannot normalize on GPU')
        if self.active and float32(sum(float32(v)**2 for v in self.colour)) == 0:
            raise ValueError('Shadow light energy vanishes on GPU')
        if self.reach is not None and float32(self.reach) <= 0:
            raise ValueError('Shadow range vanishes on GPU')


def head_lights(dimensions):
    """Preserve the existing four shader positions, without a head transform."""
    width, depth, height = vector3(dimensions)
    return tuple(ShadowLight('head', index, position, direction, (1., 1., 1.))
                 for index, (position, direction) in enumerate((
                     ((-width/2, height, 0), (1, -1, 0)),
                     ((width/2, height, 0), (-1, -1, 0)),
                     ((0, height, -depth/2), (0, -1, 1)),
                     ((0, height, depth/2), (0, -1, -1)))))


def attached_light(index, values, tip, world):
    """Freeze the original CAD-to-world delivery after light_values validation."""
    position, direction, colour, reach = values
    local = tuple(p - t for p, t in zip(vector3(position), vector3(tip), strict=True))
    origin = vector3(world)
    direction = vector3(direction)
    return ShadowLight('attached', index,
                       (local[0]+origin[0], local[2]+origin[1], -local[1]+origin[2]),
                       (direction[0], direction[2], -direction[1]), colour, reach)


@dataclass(frozen=True)
class ShadowMapPlan:
    lights: tuple
    retained_bytes: int = 0

    def __post_init__(self):
        values = tuple(self.lights)
        if (len(values) > MAX_LIGHTS or any(not isinstance(light, ShadowLight) for light in values) or
                len({(light.kind, light.index) for light in values}) != len(values)):
            raise ValueError('Shadow map consumers are incomplete or duplicated')
        if type(self.retained_bytes) is not int or self.retained_bytes < 0:
            raise ValueError('Retained shadow storage is invalid')
        # Zero-energy sources contribute no direct term, so require no map.
        # No other source is dropped to make the complete demand fit.
        object.__setattr__(self, 'lights', tuple(light for light in values if light.active))
        if self.nbytes > MAX_MAP_BYTES:
            raise ValueError('Complete point-shadow storage exceeds budget')
        for light in self.lights:
            light.certify_gpu()

    @property
    def nbytes(self):
        cube = 6 * MAP_SIZE * MAP_SIZE * 4
        # Two complete sets plus one reusable depth face; no colour attachment.
        return (len(self.lights) * cube * 2 +
                (MAP_SIZE * MAP_SIZE * 4 if self.lights else 0) + self.retained_bytes)


@dataclass(frozen=True)
class ShadowProjection:
    origin: tuple
    near: float
    far: float

    def __post_init__(self):
        object.__setattr__(self, 'origin', vector3(self.origin))
        near, far = float(self.near), float(self.far)
        if not math.isfinite(near) or not math.isfinite(far) or not 0 < near < far <= 10000:
            raise ValueError('Point-shadow projection interval is invalid')
        gpu_near, gpu_far = float32(near), float32(far)
        if not 0 < gpu_near < gpu_far:
            raise ValueError('Point-shadow projection interval collapses on GPU')
        # Certify the actual projection matrix coefficients before upload.
        float32((gpu_far+gpu_near)/(gpu_far-gpu_near))
        float32(2*gpu_far*gpu_near/(gpu_far-gpu_near))
        for value in self.origin:
            float32(value)
        object.__setattr__(self, 'near', near)
        object.__setattr__(self, 'far', far)

    def depth(self, point):
        """Standard 90-degree cube projected depth, NOT radial ray distance.

        The caller must certify the caster/receiver interval. An out-of-range
        point is unavailable, never silently classified as lit or occluded.
        """
        distance = max(abs(p - o) for p, o in zip(vector3(point), self.origin, strict=True))
        if not self.near <= distance <= self.far:
            raise ValueError('Point lies outside the shadow projection interval')
        return self.far / (self.far - self.near) * (1 - self.near / distance)

    @property
    def depth_coefficients(self):
        """The exact float coefficients shared by caster and receiver shaders.

        Caster clipZ = a*forward+b and clipW = forward. Consumers compare
        projected depths rather than amplifying storage error by inversion.
        """
        near, far = float32(self.near), float32(self.far)
        return (float32((far+near)/(far-near)), float32(-2*far*near/(far-near)))

    def certify_precision(self, receiver_far, comparison_allowance, max_world_error, *, measurement_error):
        """Refuse a rounding allowance that hides physical shadow detail.

        The coordinator supplies separate total reference/raster measurement
        error and comparison allowance, furthest dominant-axis receiver depth
        and permissible Euclidean world uncertainty from the frozen scene.
        This is not a global epsilon or a guessed caster interval.
        """
        receiver_far, comparison_allowance, max_world_error, measurement_error = map(float,
            (receiver_far, comparison_allowance, max_world_error, measurement_error))
        if (not all(math.isfinite(v) for v in (receiver_far, comparison_allowance, max_world_error, measurement_error)) or
                not self.near <= receiver_far <= self.far or comparison_allowance < 0 or
                measurement_error < 0 or max_world_error < 0):
            raise ValueError('Point-shadow precision certificate is invalid')
        error = float32(comparison_allowance)
        if error < comparison_allowance:
            raise ValueError('Point-shadow error bound rounds downward on GPU')
        # Monotonic rounding to an F32 reference means allowance>=the full
        # measurement error prevents a certified self surface darkening. A
        # world uncertainty bound alone only addresses false illumination.
        if error < measurement_error:
            raise ValueError('Point-shadow physical precision cannot prevent self-shadow')
        _, b = self.depth_coefficients
        # stored<=1. The FP32 stored+allowance addition can round upward by
        # half an ULP at1+allowance, beyond the nominal uploaded allowance.
        rounding = math.ldexp(1., math.frexp(1.+error)[1]-25) if error else 0.
        total = Fraction(measurement_error) + Fraction(error) + Fraction(rounding)
        z, k = Fraction(receiver_far), Fraction(-b/2)
        denominator = k-total*z
        if denominator <= 0:
            raise ValueError('Point-shadow projection cannot preserve physical precision')
        # Exact rational admission avoids inverse subtraction cancellation.
        # Cube forward error becomes Euclidean ray error by at most sqrt3.
        ray_scale = Fraction(math.nextafter(math.sqrt(3), math.inf))
        uncertainty = total*z*z/denominator*ray_scale
        if uncertainty > Fraction(max_world_error):
            raise ValueError('Point-shadow projection cannot preserve physical precision')
        return error
