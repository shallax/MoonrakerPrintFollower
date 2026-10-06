"""Reproducible toolhead lighting scenes in the ordinary capture pipeline.

Uses the production head and scene-lighting GLSL, the checked-in CAD palette,
and fixed lights/cameras. Cube contours are a deterministic print illustration
sectioned from Voron's STL, not a printer job or a screenshot of a live profile.
No CuraApplication, network, user configuration, or printer is involved.

Linux uses pinned Mesa/EGL software rasterisation for canonical CI pixels;
native smoke captures use the host OpenGL driver. This is capture-only tooling.
"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import struct
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpf.geometry.ToolheadGeometry import mesh_from_arrays, preview_buffer
from mpf.geometry.ToolheadLighting import light_values, validated_lights

FIXTURE = ROOT / "tests" / "fixtures" / "toolhead" / "showcase"
SIZE = (1400, 1100)
LAYER_HEIGHT = 0.2
PRINT_HEIGHT = 18.0


def cube_segments():
    """Section the original STL into horizontal deposition contours.

    Half-open edge crossing avoids duplicated coplanar/vertex intersections.
    The logo recesses and holes remain actual contours, not a box stand-in.
    """
    raw = (FIXTURE / "Voron_Design_Cube_v7.stl").read_bytes()
    count = struct.unpack_from("<I", raw, 80)[0]
    if len(raw) != 84 + count * 50:
        raise ValueError("Unexpected cube STL layout")
    dtype = np.dtype([("normal", "<f4", 3), ("points", "<f4", (3, 3)), ("attribute", "<u2")])
    triangles = np.frombuffer(raw, dtype=dtype, offset=84)["points"].astype(float)
    result = []
    for layer in range(round(PRINT_HEIGHT / LAYER_HEIGHT)):
        z = (layer + 1) * LAYER_HEIGHT
        # Slice through each layer's centre; put its top at the nozzle plane.
        plane = z - LAYER_HEIGHT / 2
        selected = triangles[(triangles[:, :, 2].min(axis=1) <= plane)
                             & (triangles[:, :, 2].max(axis=1) > plane)]
        for triangle in selected:
            hits = []
            for i, j in ((0, 1), (1, 2), (2, 0)):
                a, b = triangle[i], triangle[j]
                if (a[2] <= plane < b[2]) or (b[2] <= plane < a[2]):
                    hits.append(a + (b - a) * ((plane - a[2]) / (b[2] - a[2])))
            if len(hits) == 2 and np.linalg.norm(hits[1][:2] - hits[0][:2]) > 1e-6:
                points = np.asarray(hits)
                points[:, 2] = z - LAYER_HEIGHT / 2
                result.append(points)
    return np.asarray(result, dtype=np.float32)


def tubes(segments, width=.42, height=LAYER_HEIGHT):
    """Elliptical deposition tubes: visible walls, recesses and top contours."""
    direction = segments[:, 1] - segments[:, 0]
    side = np.stack((-direction[:, 1], direction[:, 0], np.zeros(len(direction))), axis=1)
    side /= np.maximum(np.linalg.norm(side, axis=1), 1e-9)[:, None]
    angles = np.arange(8) * (2 * np.pi / 8)
    offsets = (side[:, None, :] * (np.cos(angles) * width / 2)[None, :, None]
               + np.array((0, 0, 1)) * (np.sin(angles) * height / 2)[None, :, None])
    a, b = segments[:, 0, None, :] + offsets, segments[:, 1, None, :] + offsets
    triangles = []
    for i in range(8):
        j = (i + 1) % 8
        triangles.extend((np.stack((a[:, i], b[:, j], b[:, i]), axis=1),
                          np.stack((a[:, i], a[:, j], b[:, j]), axis=1)))
    return np.concatenate(triangles).astype(np.float32)


def quad(x0, y0, x1, y1, z):
    return np.array([[(x0, y0, z), (x1, y0, z), (x1, y1, z)],
                     [(x0, y0, z), (x1, y1, z), (x0, y1, z)]], dtype=np.float32)


def scene_meshes():
    with np.load(FIXTURE / "stealthburner.npz", allow_pickle=False) as data:
        head = mesh_from_arrays(data["triangles"], data["colours"], data["surfaces"])
    setup = json.loads((FIXTURE / "lighting.json").read_text(encoding="utf-8"))
    lights = validated_lights(setup["toolhead_lights"])
    if len(lights) != 5:
        raise ValueError("The showcase must retain its five reference lights")
    # Model coordinates stay untouched so the persisted surface IDs/positions
    # match the real settings editor. Put the nozzle on the cube's right rim.
    tip = np.asarray(setup["toolhead_tip"] or head.automatic_tip)
    offset = tip - np.array((29.79, 15, PRINT_HEIGHT))
    segments = cube_segments() + offset
    paths = mesh_from_arrays(tubes(segments), np.tile((.37, .39, .42, 1), (len(segments)*16, 1)))
    bed_z = offset[2] - .025
    plate = [quad(-125, -125, 125, 125, bed_z)]
    colours = [np.tile((.15, .165, .19, 1), (2, 1))]
    for axis in range(-120, 121, 10):
        plate.extend((quad(axis-.055, -125, axis+.055, 125, bed_z+.01),
                      quad(-125, axis-.055, 125, axis+.055, bed_z+.01)))
        colours.extend((np.tile((.22, .24, .27, 1), (2, 1)),)*2)
    bed = mesh_from_arrays(np.concatenate(plate), np.concatenate(colours))
    return head, paths, bed, lights


def program(context, filename):
    source = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    source.read(ROOT / "mpf" / "toolhead" / filename, encoding="utf-8")
    return context.program(vertex_shader=source["shaders"]["vertex41core"],
                           fragment_shader=source["shaders"]["fragment41core"])


def uniforms(shader, values):
    for name, value in values.items():
        if name not in shader:
            continue
        if isinstance(value, np.ndarray):
            shader[name].write(value.astype("f4").tobytes())
        else:
            shader[name].value = value


def lighting_uniforms(lights):
    values = {"u_attachedCount": len(lights), "u_lightOpacity": 1., "u_opacity": 1.,
              "u_depthOnly": 0, "u_lightingEnabled": 1, "u_hasColour": 1}
    positions, directions, colours, ranges, surfaces, paints = [], [], [], [], [], []
    for light in lights:
        position, direction, colour, reach = light_values(light)
        positions.append(position)
        directions.append(direction)
        colours.append(colour)
        ranges.append(reach)
        surfaces.append(light["surface"])
        paints.append((*[int(light["colour"][i:i+2], 16)/255 for i in (1, 3, 5)], float(light["paint"])))
    for name, rows, width in (("Position", positions, 3), ("Direction", directions, 3),
                              ("Colour", colours, 3), ("Range", ranges, 1),
                              ("Surface", surfaces, 1), ("Paint", paints, 4)):
        array = np.zeros((8, width), dtype=np.float32)
        array[:len(rows)] = np.asarray(rows).reshape(-1, width)
        values["u_attached" + name] = array
    # Four white perimeter strips, at the top of a 250 mm build volume,
    # pointing down/inward at 45 degrees, like the live toolhead renderer.
    for i, (position, direction) in enumerate((((-125, 0, 232), (1, 0, -1)),
                                               ((125, 0, 232), (-1, 0, -1)),
                                               ((0, -125, 232), (0, 1, -1)),
                                               ((0, 125, 232), (0, -1, -1)))):
        values["u_light" + str(i)] = position
        values["u_direction" + str(i)] = direction
    return values


def camera(target, yaw, pitch, height):
    yaw, pitch = np.deg2rad((yaw, pitch))
    right = np.array((np.cos(yaw), np.sin(yaw), 0))
    up = np.array((-np.sin(yaw)*np.sin(pitch), np.cos(yaw)*np.sin(pitch), np.cos(pitch)))
    depth = np.cross(right, up)
    target = np.asarray(target)
    rows = np.eye(4)
    rows[0, :3] = right / (height * SIZE[0] / SIZE[1] / 2)
    rows[1, :3] = up / (height / 2)
    rows[2, :3] = -depth / 500
    rows[:3, 3] = -rows[:3, :3] @ target
    return rows.T, tuple(target + depth*500)


def capture(output_dir):
    import moderngl
    from PyQt6.QtGui import QImage

    dll_directory = None
    if sys.platform == "win32" and os.environ.get("GLCONTEXT_WIN_LIBGL"):
        dll_directory = os.add_dll_directory(str(Path(os.environ["GLCONTEXT_WIN_LIBGL"]).parent))
    if sys.platform.startswith("linux"):
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
        os.environ["GALLIUM_DRIVER"] = "llvmpipe"
        os.environ["LP_NUM_THREADS"] = "1"
        # Keep native CI x86 and emulated x86 on the same arithmetic path.
        os.environ["GALLIUM_OVERRIDE_CPU_CAPS"] = "sse2"
        os.environ["LP_NATIVE_VECTOR_WIDTH"] = "128"
        context = moderngl.create_context(standalone=True, require=410, backend="egl")
    else:
        context = moderngl.create_context(standalone=True, require=410)
    print("Toolhead capture renderer:", context.info["GL_RENDERER"], flush=True)
    head, paths, bed, lights = scene_meshes()
    head_shader = program(context, "toolhead.shader")
    # Base receiver shading is a neutral deterministic stand-in for native
    # SimulationView's material palette; additive lighting is production GLSL.
    source = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    source.read(ROOT / "mpf" / "toolhead" / "scene-lighting.shader", encoding="utf-8")
    fragment = source["shaders"]["fragment41core"]
    output = "frag_color = vec4(lightSurface(f_vertex, f_normal, f_color.rgb), f_color.a);"
    if fragment.count(output) != 1:
        raise RuntimeError("Scene shader output changed; update the capture base-colour adapter")
    # Compose the neutral base and unchanged production lightSurface function
    # in one pass. Separate compiled vertex programs need not produce bitwise
    # identical depth; using LEQUAL between them can stripe tiny layer tubes.
    fragment = fragment.replace(output, """float diffuse=max(dot(normalize(f_normal),normalize(vec3(-.4,-.5,1.))),0.);
frag_color=vec4(f_color.rgb*(.5+.5*diffuse)+lightSurface(f_vertex,f_normal,f_color.rgb),f_color.a);""")
    base_shader = context.program(vertex_shader=source["shaders"]["vertex41core"], fragment_shader=fragment)
    buffers, vaos = [], {}
    for label, mesh in (("head", head), ("paths", paths), ("bed", bed)):
        buffer = context.buffer(preview_buffer(mesh)[0])
        buffers.append(buffer)
        for shader in ((head_shader,) if label == "head" else (base_shader,)):
            if shader is head_shader:
                layout, attrs = "3f 3f 4f 1f", ("a_vertex", "a_normal", "a_color", "a_surface")
            else:
                layout, attrs = "3f 3f 4f 4x", ("a_vertex", "a_normal", "a_color")
            vaos[label, shader] = context.vertex_array(shader, [(buffer, layout, *attrs)])
    framebuffer = context.simple_framebuffer(SIZE, components=4, samples=4)
    resolved = context.simple_framebuffer(SIZE, components=4)
    framebuffer.use()
    context.enable(moderngl.DEPTH_TEST)
    context.disable(moderngl.CULL_FACE)
    light_values_ = lighting_uniforms(lights)
    identity = np.eye(4, dtype=np.float32)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scenes = (("13-toolhead-lighting.png", (0, 8, 61), 12, -10, 170, False),
              ("14-toolhead-printing.png", (-12, 0, 10), -28, 7, 80, True))
    for filename, target, yaw, pitch, height, receivers in scenes:
        projection, eye = camera(target, yaw, pitch, height)
        for shader in (head_shader, base_shader):
            uniforms(shader, dict(light_values_, u_modelMatrix=identity, u_normalMatrix=identity,
                                  u_viewMatrix=identity, u_projectionMatrix=projection, u_viewPosition=eye))
        framebuffer.use()
        framebuffer.depth_mask = True
        framebuffer.clear(.09, .105, .125, 1, depth=1)
        context.depth_func = "<"
        context.disable(moderngl.BLEND)
        if receivers:
            for label in ("bed", "paths"):
                vaos[label, base_shader].render()
        # The nearest head surface receives its intrinsic CAD alpha exactly as
        # in the settings preview; no geometry order dependent double blending.
        framebuffer.color_mask = (False, False, False, False)
        head_shader["u_depthOnly"].value = 1
        vaos["head", head_shader].render()
        framebuffer.color_mask = (True, True, True, True)
        context.depth_func = "<="
        framebuffer.depth_mask = False
        context.enable(moderngl.BLEND)
        context.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA, moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
        head_shader["u_depthOnly"].value = 0
        vaos["head", head_shader].render()
        context.copy_framebuffer(resolved, framebuffer)
        raw = resolved.read(components=4, alignment=1)
        image = QImage(raw, *SIZE, SIZE[0]*4, QImage.Format.Format_RGBA8888).flipped()
        pixels = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4)
        if np.count_nonzero(pixels[:, 0] > pixels[:, 1]*1.5) < 1000:
            raise RuntimeError("Toolhead render lacks the expected red CAD body")
        if not image.save(str(output_dir / filename)):
            raise RuntimeError("Could not save toolhead capture")
        print(filename, flush=True)
    for vao in vaos.values():
        vao.release()
    for buffer in buffers:
        buffer.release()
    for shader in (head_shader, base_shader):
        shader.release()
    framebuffer.release()
    resolved.release()
    context.release()
    if dll_directory is not None:
        dll_directory.close()


if __name__ == "__main__":
    capture(sys.argv[1])
