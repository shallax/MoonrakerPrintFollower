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
    bed = mesh_from_arrays(quad(-125, -125, 125, 125, bed_z),
                           np.tile((.15, .165, .19, 1), (2, 1)))
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


def create_context():
    """Use the same headless driver for shader tests and showcase captures."""
    import moderngl

    dll_directory = None
    if sys.platform == "win32" and os.environ.get("GLCONTEXT_WIN_LIBGL"):
        dll_directory = os.add_dll_directory(str(Path(os.environ["GLCONTEXT_WIN_LIBGL"]).parent))
        # The headless runner otherwise selects Mesa's D3D12 adapter over
        # Microsoft's Basic Render Driver, which crashes during rendering.
        # Use Mesa's CPU rasteriser for capture evidence, as Linux does.
        os.environ["GALLIUM_DRIVER"] = "llvmpipe"
        os.environ["LP_NUM_THREADS"] = "1"
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
    return context, dll_directory


def render_receivers(bed, paths, shader):
    """Sample the grid on the plane, avoiding thin-strip MSAA coverage drift."""
    shader["u_captureGrid"].value = 1
    try:
        bed.render()
    finally:
        shader["u_captureGrid"].value = 0
    paths.render()


def capture_sample_scale(renderer):
    # Apple's software MSAA can shade a tube-facet boundary inconsistently.
    # Four fixed floating samples resolve once on CPU, without driver rounding.
    return 2 if renderer == "Apple Software Renderer" else 1


def resolve_capture_samples(raw, scale):
    if scale == 1:
        return raw
    if scale != 2:
        raise ValueError("Unsupported capture sample scale")
    samples = np.frombuffer(raw, dtype=np.float32).reshape(SIZE[1], 2, SIZE[0], 2, 4)
    if not np.isfinite(samples).all():
        raise ValueError("Nonfinite capture samples")
    # RGBA8 clamps each sample before resolving. Preserve that operation, with
    # one explicitly rounded final conversion instead of four driver conversions.
    averaged = np.clip(samples, 0.0, 1.0).mean((1, 3), dtype=np.float64)
    return np.floor(averaged * 255.0 + 0.5).astype(np.uint8).tobytes()


def capture_bed_uniforms(projection, z, viewport):
    # Solve the plane using the exact FLOAT32 matrix uploaded to the driver.
    rows = np.asarray(projection, dtype=np.float32).astype(np.float64).T
    if not np.isfinite(rows).all() or not np.isfinite(z):
        raise ValueError("Nonfinite analytic showcase camera")
    if not np.array_equal(rows[3], (0.0, 0.0, 0.0, 1.0)):
        raise ValueError("Analytic showcase coverage requires the fixed orthographic camera")
    inverse = np.linalg.inv(rows[:2, :2])
    constant = -inverse @ (rows[:2, 2] * z + rows[:2, 3])
    return dict(u_captureBedX=tuple((*inverse[0], constant[0])),
                u_captureBedY=tuple((*inverse[1], constant[1])),
                u_captureBedZ=float(z), u_captureViewport=viewport)


def capture_receiver_vertex(vertex, renderer):
    if renderer != "Apple Software Renderer":
        return vertex
    if vertex.count("#version 410") != 1 or vertex.count("gl_Position =") != 1:
        raise RuntimeError("Receiver vertex shader changed; update the capture position adapter")
    # The geometry stage supplies conservative support; the fragment stage
    # tests the original edges. A driver triangle edge crack cannot then expose
    # the bright backside of an otherwise continuous deposition tube.
    vertex = vertex.replace("#version 410", "#version 410\ninvariant gl_Position;\nprecise gl_Position;")
    for name in ("vertex", "normal", "color"):
        vertex = vertex.replace("f_" + name, "g_" + name)
    return vertex


def capture_receiver_geometry(renderer):
    if renderer != "Apple Software Renderer":
        return None
    return """#version 410
layout(triangles) in;
layout(triangle_strip, max_vertices=3) out;
uniform vec2 u_captureViewport;
in vec3 g_vertex[]; in vec3 g_normal[]; in vec4 g_color[];
flat out dvec3 captureEdges[3];
flat out vec3 captureVertices[3];
flat out vec3 captureNormals[3];
flat out vec4 captureColours[3];
flat out dvec3 captureDepths;
flat out double captureArea;
flat out ivec3 captureInclusive;

// Evaluate shared endpoints in one canonical order, then negate the complete
// equation for the opposite orientation. Both adjacent facets use exactly the
// same arithmetic, including at the problematic near-edge supersample.
dvec3 edgeEquation(dvec2 a, dvec2 b) {
    bool reverse = a.x > b.x || (a.x == b.x && a.y > b.y);
    dvec2 low = reverse ? b : a;
    dvec2 high = reverse ? a : b;
    precise dvec3 edge = dvec3(low.y-high.y, high.x-low.x,
                             low.x*high.y-low.y*high.x);
    return reverse ? -edge : edge;
}
void main() {
    dvec2 point[3];
    for(int i=0;i<3;++i)
        point[i]=(dvec2(gl_in[i].gl_Position.xy)*0.5+0.5)*dvec2(u_captureViewport);
    precise double area = dot(edgeEquation(point[0],point[1]),dvec3(point[2],1.0));
    if(area==0.0) return;
    int order[3]; order[0]=0; order[1]=area>0.0 ? 1 : 2; order[2]=area>0.0 ? 2 : 1;
    dvec2 p[3];
    for(int i=0;i<3;++i) {
        int j=order[i]; p[i]=point[j];
    }
    dvec3 edges[3]; ivec3 inclusive;
    for(int i=0;i<3;++i) {
        dvec2 a=p[(i+1)%3], b=p[(i+2)%3];
        edges[i]=edgeEquation(a,b);
        dvec2 delta=b-a;
        inclusive[i]=(delta.y>0.0 || (delta.y==0.0 && delta.x<0.0)) ? 1 : 0;
    }
    // Expand support only. Analytic coverage below discards everything outside
    // the original triangle and writes the original interpolated depth.
    dvec2 low=floor(min(p[0],min(p[1],p[2])))-1.0;
    dvec2 high=ceil(max(p[0],max(p[1],p[2])))+1.0;
    // One enclosing support triangle has no internal shared diagonal that the
    // software rasterizer could crack before original-edge testing runs.
    dvec2 extent=high-low;
    dvec2 corner[3]; corner[0]=low;
    corner[1]=low+dvec2(2.0*extent.x,0.0);
    corner[2]=low+dvec2(0.0,2.0*extent.y);
    for(int i=0;i<3;++i) {
        // EmitVertex makes every output undefined, even flat values. Republish
        // the original triangle payload for every support vertex.
        captureArea=abs(area); captureInclusive=inclusive;
        for(int k=0;k<3;++k) {
            int j=order[k]; captureEdges[k]=edges[k];
            captureVertices[k]=g_vertex[j]; captureNormals[k]=g_normal[j];
            captureColours[k]=g_color[j]; captureDepths[k]=double(gl_in[j].gl_Position.z)*0.5+0.5;
        }
        gl_Position=vec4(vec2(corner[i]/dvec2(u_captureViewport)*2.0-1.0),0.0,1.0);
        EmitVertex();
    }
    EndPrimitive();
}
"""


def capture_receiver_coverage(fragment, renderer):
    if renderer != "Apple Software Renderer":
        return fragment
    declarations = """flat in dvec3 captureEdges[3];
flat in vec3 captureVertices[3]; flat in vec3 captureNormals[3];
flat in vec4 captureColours[3]; flat in dvec3 captureDepths;
flat in double captureArea; flat in ivec3 captureInclusive;
"""
    for field in ("vec3 f_vertex", "vec3 f_normal", "vec4 f_color"):
        if fragment.count("in " + field + ";") != 1:
            raise RuntimeError("Receiver fragment inputs changed; update the analytic coverage adapter")
        fragment = fragment.replace("in " + field + ";", field + ";")
    if fragment.count("void main() {") != 1:
        raise RuntimeError("Receiver fragment entry changed; update the analytic coverage adapter")
    reconstruction = """void main() {
    precise dvec3 samplePoint=dvec3(gl_FragCoord.xy,1.0);
    precise dvec3 edges;
    for(int i=0;i<3;++i) {
        edges[i]=dot(captureEdges[i],samplePoint);
        if(edges[i]<0.0 || (edges[i]==0.0 && captureInclusive[i]==0)) discard;
    }
    precise dvec3 barycentric=edges/captureArea;
    precise double originalDepth=dot(barycentric,captureDepths);
    if(originalDepth<0.0 || originalDepth>1.0) discard;
    gl_FragDepth=float(originalDepth);
    f_vertex=vec3(barycentric.x*dvec3(captureVertices[0])+barycentric.y*dvec3(captureVertices[1])+barycentric.z*dvec3(captureVertices[2]));
    f_normal=vec3(barycentric.x*dvec3(captureNormals[0])+barycentric.y*dvec3(captureNormals[1])+barycentric.z*dvec3(captureNormals[2]));
    f_color=vec4(barycentric.x*dvec4(captureColours[0])+barycentric.y*dvec4(captureColours[1])+barycentric.z*dvec4(captureColours[2]));
"""
    return fragment.replace("#version 410", "#version 410\n" + declarations).replace("void main() {", reconstruction)


def capture_receiver_fragment(fragment, renderer):
    if renderer != "Apple Software Renderer":
        return fragment  # Preserve the exact canonical/native shader source.
    fragment = fragment.replace("uniform bool u_captureGrid;", """uniform bool u_captureGrid;
uniform vec3 u_captureBedX; uniform vec3 u_captureBedY;
uniform float u_captureBedZ; uniform vec2 u_captureViewport;""")
    start = fragment.index("vec3 baseColour=f_color.rgb;")
    tail = fragment[start:].replace("f_vertex", "capturePosition").replace("f_normal", "captureNormal")
    # The fixed plane has an exact pixel footprint. Derivatives after analytic
    # edge discard would be undefined along the original triangle diagonal.
    tail = tail.replace("fwidth(capturePosition.xy)", """vec2(
        2.0*(abs(u_captureBedX.x)/u_captureViewport.x+abs(u_captureBedX.y)/u_captureViewport.y),
        2.0*(abs(u_captureBedY.x)/u_captureViewport.x+abs(u_captureBedY.y)/u_captureViewport.y))""")
    # Software varying interpolation drifts at byte-rounding boundaries even
    # in a single-sample target. Reconstruct the same physical bed plane from
    # fixed pixel coordinates; raster coverage and depth remain unchanged.
    return fragment[:start] + """precise vec3 capturePosition=f_vertex;
vec3 captureNormal=f_normal;
if(u_captureGrid){
    precise vec2 ndc=(gl_FragCoord.xy/u_captureViewport)*2.0-1.0;
    precise float px=ndc.x*u_captureBedX.x+ndc.y*u_captureBedX.y+u_captureBedX.z;
    precise float py=ndc.x*u_captureBedY.x+ndc.y*u_captureBedY.y+u_captureBedY.z;
    capturePosition=vec3(px,py,u_captureBedZ);
    captureNormal=vec3(0.0,0.0,1.0);
}
""" + tail


def capture_receiver_sources(vertex, fragment, renderer, *, planar=False):
    """Keep the large bed on native raster coverage; adapt only tube facets."""
    fragment = capture_receiver_fragment(fragment, renderer)
    return dict(vertex_shader=vertex if planar else capture_receiver_vertex(vertex, renderer),
                geometry_shader=None if planar else capture_receiver_geometry(renderer),
                fragment_shader=fragment if planar else capture_receiver_coverage(fragment, renderer))


def capture(output_dir):
    import moderngl
    from PyQt6.QtGui import QImage

    context, dll_directory = create_context()
    renderer = context.info["GL_RENDERER"]
    print("Toolhead capture renderer:", renderer, flush=True)
    head, paths, bed, lights = scene_meshes()
    if np.any(head.colours[:, 3] != 1.0):
        raise ValueError("The showcase capture requires opaque CAD materials")
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
    if fragment.count("#version 410") != 1:
        raise RuntimeError("Scene shader version changed; update the capture grid adapter")
    fragment = fragment.replace("#version 410", "#version 410\nuniform bool u_captureGrid;")
    fragment = fragment.replace(output, """vec3 baseColour=f_color.rgb;
if(u_captureGrid){
    vec2 distanceToGrid=abs(mod(f_vertex.xy+vec2(5.0),vec2(10.0))-vec2(5.0));
    vec2 footprint=max(fwidth(f_vertex.xy),vec2(1e-6));
    vec2 coverage=vec2(1.0)-smoothstep(vec2(.055)-.5*footprint,vec2(.055)+.5*footprint,distanceToGrid);
    baseColour=mix(baseColour,vec3(.22,.24,.27),max(coverage.x,coverage.y));
}
float diffuse=max(dot(normalize(f_normal),normalize(vec3(-.4,-.5,1.))),0.);
frag_color=vec4(baseColour*(.5+.5*diffuse)+lightSurface(f_vertex,f_normal,baseColour),f_color.a);""")
    vertex = source["shaders"]["vertex41core"]
    base_shader = context.program(**capture_receiver_sources(vertex, fragment, renderer))
    bed_shader = (context.program(**capture_receiver_sources(vertex, fragment, renderer, planar=True))
                  if renderer == "Apple Software Renderer" else base_shader)
    receiver_shaders = (base_shader, bed_shader) if bed_shader is not base_shader else (base_shader,)
    buffers, vaos = [], {}
    for label, mesh in (("head", head), ("paths", paths), ("bed", bed)):
        buffer = context.buffer(preview_buffer(mesh)[0])
        buffers.append(buffer)
        for shader in ((head_shader,) if label == "head" else (bed_shader,) if label == "bed" else (base_shader,)):
            if shader is head_shader:
                layout, attrs = "3f 3f 4f 1f 4f 1f 2f", ("a_vertex", "a_normal", "a_color", "a_surface", "a_material", "a_body", "a_finish")
            else:
                layout, attrs = "3f 3f 4f 32x", ("a_vertex", "a_normal", "a_color")
            vaos[label, shader] = context.vertex_array(shader, [(buffer, layout, *attrs)])
    sample_scale = capture_sample_scale(renderer)
    sample_dtype = "f4" if sample_scale == 2 else "f1"
    render_size = tuple(value * sample_scale for value in SIZE)
    framebuffer = context.simple_framebuffer(render_size, components=4,
                                           samples=4 if sample_scale == 1 else 0, dtype=sample_dtype)
    resolved = context.simple_framebuffer(render_size, components=4, dtype=sample_dtype)
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
        if sample_scale == 2:
            # Validate the uploaded projection before any rendering, including
            # the head-only scene. Model/view transforms here are identity.
            values = capture_bed_uniforms(projection, float(bed.triangles[0, 0, 2]), render_size)
            for shader in receiver_shaders:
                uniforms(shader, values)
        for shader in (head_shader, *receiver_shaders):
            uniforms(shader, dict(light_values_, u_modelMatrix=identity, u_normalMatrix=identity,
                                  u_viewMatrix=identity, u_projectionMatrix=projection, u_viewPosition=eye,
                                  u_orthographic=1, u_viewDirection=tuple((np.asarray(eye)-target)/500)))
        framebuffer.use()
        framebuffer.depth_mask = True
        framebuffer.clear(.09, .105, .125, 1, depth=1)
        context.depth_func = "<"
        context.disable(moderngl.BLEND)
        if receivers:
            render_receivers(vaos["bed", bed_shader], vaos["paths", base_shader], bed_shader)
        # The reference CAD materials are opaque. A single shaded,
        # depth-writing pass avoids cross-pass MSAA coverage mismatches on
        # Apple's software renderer; equal-depth faces retain draw order.
        context.depth_func = "<="
        head_shader["u_depthOnly"].value = 0
        vaos["head", head_shader].render()
        context.copy_framebuffer(resolved, framebuffer)
        raw = resolve_capture_samples(resolved.read(components=4, alignment=1, dtype=sample_dtype), sample_scale)
        image = QImage(raw, *SIZE, SIZE[0]*4, QImage.Format.Format_RGBA8888).flipped()
        pixels = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4)
        if np.any(pixels[:, 3] != 255):
            raise RuntimeError("Opaque toolhead capture contains transparent samples")
        if np.count_nonzero(pixels[:, 0] > pixels[:, 1]*1.5) < 1000:
            raise RuntimeError("Toolhead render lacks the expected red CAD body")
        if not image.save(str(output_dir / filename)):
            raise RuntimeError("Could not save toolhead capture")
        print(filename, flush=True)
    for vao in vaos.values():
        vao.release()
    for buffer in buffers:
        buffer.release()
    for shader in (head_shader, *receiver_shaders):
        shader.release()
    framebuffer.release()
    resolved.release()
    context.release()
    if dll_directory is not None:
        dll_directory.close()


if __name__ == "__main__":
    capture(sys.argv[1])
