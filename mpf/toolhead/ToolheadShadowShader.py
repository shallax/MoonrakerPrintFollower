"""Optional owned raster variants; shadow only existing direct-light terms.

The base sources remain byte-identical when shadows are disabled. A coordinator
must certify complete map/receiver intervals, sampler units and frozen camera
values before enabling a slot; this module never publishes map availability.
"""


def _replace(source, before, after):
    if source.count(before) != 1:
        raise ValueError('Direct-light shader contract changed: ' + before)
    return source.replace(before, after)


def visibility_source(count, legacy):
    """Separate named cube samplers also compile under GLSL120."""
    if count not in (8, 12):
        raise ValueError('Shadow shader consumer count is invalid')
    sample = 'textureCube' if legacy else 'textureLod'
    uniforms = '\n'.join('uniform samplerCube u_shadowMap%d;' % index for index in range(count))
    arguments = 'direction' if legacy else 'direction, 0.0'
    branches = '\n'.join('if (slot == %d) return %s(u_shadowMap%d, %s).r;' % (index, sample, index, arguments)
                         for index in range(count))
    return '''
uniform int u_shadowValid[COUNT];
uniform vec3 u_shadowClip[COUNT];
uniform vec2 u_shadowProjection[COUNT];
uniform float u_shadowDepthError[COUNT];
UNIFORMS
float shadowStoredDepth(int slot, vec3 direction) {
    BRANCHES
    return 1.0;
}
float shadowVisibility(int slot, vec3 light, vec3 surface) {
    if (u_shadowValid[slot] == 0) return 1.0;
    vec3 delta = surface - light;
    float forward = max(max(abs(delta.x), abs(delta.y)), abs(delta.z));
    float stored = shadowStoredDepth(slot, delta);
    if (stored >= 1.0) return 1.0;
    vec3 clip = u_shadowClip[slot];
    float biasedForward = max(forward - clip.z, clip.x);
    vec2 projection = u_shadowProjection[slot];
    float reference = (projection.x * biasedForward + projection.y) / biasedForward * 0.5 + 0.5;
    return reference <= stored + u_shadowDepthError[slot] ? 1.0 : 0.0;
}
'''.replace('COUNT', str(count)).replace('UNIFORMS', uniforms).replace('BRANCHES', branches)


def _inject(source, helper):
    # #version must remain the first directive in core sources.
    stripped = source.lstrip()
    if stripped.startswith('#version'):
        first, body = stripped.split('\n', 1)
        return source[:len(source)-len(stripped)] + first + '\n' + helper + body
    return helper + source


def head_fragment(source, *, enabled, legacy=False):
    if not enabled:
        return source
    source = _replace(source, 'float led(vec3 position, vec3 direction, vec3 normal)',
                      'float led(int shadowSlot, vec3 position, vec3 direction, vec3 normal)')
    source = _replace(source, 'return max(dot(normal, toLight), 0.0) * emission;',
                      'float direct = max(dot(normal, toLight), 0.0) * emission;\n'
                      'if (direct <= 0.0) return 0.0;\n'
                      'return direct * shadowVisibility(shadowSlot, position, v_position);')
    for index in range(4):
        source = _replace(source, 'led(u_light%d,' % index, 'led(%d, u_light%d,' % (index, index))
    source = _replace(source, 'float specular = pow(max(dot(normal, halfVector), 0.0), shininess);',
                      'float specular = pow(max(dot(normal, halfVector), 0.0), shininess);\n'
                      'if (specular > 0.0) specular *= shadowVisibility(0, u_light0, v_position);')
    # Refuse an extra energy definition even outside the contiguous block.
    energy = 'float energy = outward * falloff*falloff;'
    source = _replace(source, energy, energy)
    source = _replace(source, '''float energy = outward * falloff*falloff;
illumination += u_attachedColour[i] * energy * facing * 3.0;
vec3 halfLight = normalize(toLight + eye);
float broadHighlight = pow(max(dot(normal, halfLight), 0.0), 8.0);''',
                      '''vec3 halfLight = normalize(toLight + eye);
        float broadHighlight = pow(max(dot(normal, halfLight), 0.0), 8.0);
        float energy = outward * falloff*falloff;
        if (facing > 0.0 || broadHighlight > 0.0)
            energy *= shadowVisibility(4+i, u_attachedPosition[i], v_position);
        illumination += u_attachedColour[i] * energy * facing * 3.0;''')
    return _inject(source, visibility_source(12, legacy))


def scene_fragment(source, *, enabled, legacy=False):
    if not enabled:
        return source
    source = _replace(source, 'vec3 lightSurface(vec3 position, vec3 surfaceNormal, vec3 baseColour)',
                      'vec3 lightSurface(vec3 position, vec3 surfaceNormal, vec3 baseColour, vec3 shadowPosition)')
    energy = 'float energy = outward * falloff * falloff;'
    source = _replace(source, energy, energy)
    source = _replace(source, '''float energy = outward * falloff * falloff;
float facing = max(dot(normal, toLight), 0.0);
float highlight = pow(max(dot(normal, normalize(toLight + eye)), 0.0), 8.0);''',
                      '''float facing = max(dot(normal, toLight), 0.0);
        float highlight = pow(max(dot(normal, normalize(toLight + eye)), 0.0), 8.0);
        if (facing <= 0.0 && highlight <= 0.0) continue;
        float energy = outward * falloff * falloff * shadowVisibility(i, u_attachedPosition[i], shadowPosition);''')
    source = _replace(source, 'lightSurface(f_vertex, f_normal, f_color.rgb)',
                      'lightSurface(f_vertex, f_normal, f_color.rgb, shadowReceiverPosition(gl_FragCoord.xy, gl_FragCoord.z))')
    reconstruction = '''
uniform mat4 u_shadowInverseViewProjection;
uniform vec4 u_shadowViewport;
uniform vec2 u_shadowDepthRange;
vec3 shadowReceiverPosition(vec2 raster, float depth) {
    vec2 ndc = (raster-u_shadowViewport.xy) / u_shadowViewport.zw * 2.0 - 1.0;
    float z = (depth-u_shadowDepthRange.x) / (u_shadowDepthRange.y-u_shadowDepthRange.x) * 2.0 - 1.0;
    vec4 point = u_shadowInverseViewProjection * vec4(ndc, z, 1.0);
    return point.xyz / point.w;
}
'''
    # Deferred callers use the same four-argument lightSurface, with the true
    # stored raster depth and the original source viewport/camera certificate.
    return _inject(source, visibility_source(8, legacy) + reconstruction)
