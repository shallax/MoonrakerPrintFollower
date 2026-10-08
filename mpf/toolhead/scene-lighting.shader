[shaders]
vertex =
    uniform mat4 u_modelMatrix;
    uniform mat4 u_viewMatrix;
    uniform mat4 u_projectionMatrix;
    uniform mat4 u_normalMatrix;
    uniform int u_hasColour;
    attribute vec3 a_vertex;
    attribute vec3 a_normal;
    attribute vec4 a_color;
    varying vec3 f_vertex;
    varying vec3 f_normal;
    varying vec4 f_color;
    void main() {
        vec4 world = u_modelMatrix * vec4(a_vertex, 1.0);
        gl_Position = u_projectionMatrix * u_viewMatrix * world;
        f_vertex = world.xyz;
        f_normal = (u_normalMatrix * vec4(a_normal, 0.0)).xyz;
        f_color = u_hasColour == 1 ? a_color : vec4(0.35, 0.35, 0.35, 1.0);
    }

fragment =
    varying vec3 f_vertex;
    varying vec3 f_normal;
    varying vec4 f_color;
    uniform int u_depthOnly;
    uniform float u_lightOpacity;
    uniform int u_attachedCount;
    uniform vec3 u_attachedPosition[8];
    uniform vec3 u_attachedDirection[8];
    uniform vec3 u_attachedColour[8];
    uniform float u_attachedRange[8];
    uniform vec3 u_viewPosition;
    uniform int u_orthographic;
    uniform vec3 u_viewDirection;
    vec3 lightSurface(vec3 position, vec3 surfaceNormal, vec3 baseColour) {
        vec3 normal = normalize(surfaceNormal);
        vec3 eye = u_orthographic == 1 ? normalize(u_viewDirection) : normalize(u_viewPosition - position);
        vec3 result = vec3(0.0);
        for (int i = 0; i < 8; ++i) {
            if (i >= u_attachedCount) break;
            vec3 delta = u_attachedPosition[i] - position;
            float squaredDistance = dot(delta, delta);
            if (squaredDistance >= u_attachedRange[i] * u_attachedRange[i]) continue;
            if (dot(u_attachedColour[i], u_attachedColour[i]) <= 0.0) continue;
            float distanceToLight = max(sqrt(squaredDistance), 0.001);
            vec3 toLight = delta / distanceToLight;
            float outward = max(dot(normalize(u_attachedDirection[i]), -toLight), 0.0);
            if (outward <= 0.0) continue;
            float falloff = max(1.0 - distanceToLight / u_attachedRange[i], 0.0);
            float energy = outward * falloff * falloff;
            float facing = max(dot(normal, toLight), 0.0);
            float highlight = pow(max(dot(normal, normalize(toLight + eye)), 0.0), 8.0);
            result += u_attachedColour[i] * energy * (max(baseColour, vec3(0.035)) * facing * 3.0 + highlight * 0.30);
        }
        return result * u_lightOpacity;
    }
    void main() {
        if (f_color.a <= 0.0) discard;
        if (u_depthOnly == 1) { gl_FragColor = vec4(0.0); return; }
        gl_FragColor = vec4(lightSurface(f_vertex, f_normal, f_color.rgb), f_color.a);
    }

vertex41core =
    #version 410
    uniform mat4 u_modelMatrix;
    uniform mat4 u_viewMatrix;
    uniform mat4 u_projectionMatrix;
    uniform mat4 u_normalMatrix;
    uniform int u_hasColour;
    in vec3 a_vertex;
    in vec3 a_normal;
    in vec4 a_color;
    out vec3 f_vertex;
    out vec3 f_normal;
    out vec4 f_color;
    void main() {
        vec4 world = u_modelMatrix * vec4(a_vertex, 1.0);
        gl_Position = u_projectionMatrix * u_viewMatrix * world;
        f_vertex = world.xyz;
        f_normal = (u_normalMatrix * vec4(a_normal, 0.0)).xyz;
        f_color = u_hasColour == 1 ? a_color : vec4(0.35, 0.35, 0.35, 1.0);
    }

fragment41core =
    #version 410
    in vec3 f_vertex;
    in vec3 f_normal;
    in vec4 f_color;
    out vec4 frag_color;
    uniform int u_depthOnly;
    uniform float u_lightOpacity;
    uniform int u_attachedCount;
    uniform vec3 u_attachedPosition[8];
    uniform vec3 u_attachedDirection[8];
    uniform vec3 u_attachedColour[8];
    uniform float u_attachedRange[8];
    uniform vec3 u_viewPosition;
    uniform int u_orthographic;
    uniform vec3 u_viewDirection;
    vec3 lightSurface(vec3 position, vec3 surfaceNormal, vec3 baseColour) {
        vec3 normal = normalize(surfaceNormal);
        vec3 eye = u_orthographic == 1 ? normalize(u_viewDirection) : normalize(u_viewPosition - position);
        vec3 result = vec3(0.0);
        for (int i = 0; i < 8; ++i) {
            if (i >= u_attachedCount) break;
            vec3 delta = u_attachedPosition[i] - position;
            float squaredDistance = dot(delta, delta);
            if (squaredDistance >= u_attachedRange[i] * u_attachedRange[i]) continue;
            if (dot(u_attachedColour[i], u_attachedColour[i]) <= 0.0) continue;
            float distanceToLight = max(sqrt(squaredDistance), 0.001);
            vec3 toLight = delta / distanceToLight;
            float outward = max(dot(normalize(u_attachedDirection[i]), -toLight), 0.0);
            if (outward <= 0.0) continue;
            float falloff = max(1.0 - distanceToLight / u_attachedRange[i], 0.0);
            float energy = outward * falloff * falloff;
            float facing = max(dot(normal, toLight), 0.0);
            float highlight = pow(max(dot(normal, normalize(toLight + eye)), 0.0), 8.0);
            result += u_attachedColour[i] * energy * (max(baseColour, vec3(0.035)) * facing * 3.0 + highlight * 0.30);
        }
        return result * u_lightOpacity;
    }
    void main() {
        if (f_color.a <= 0.0) discard;
        if (u_depthOnly == 1) { frag_color = vec4(0.0); return; }
        frag_color = vec4(lightSurface(f_vertex, f_normal, f_color.rgb), f_color.a);
    }

[defaults]
u_depthOnly = 0
u_lightOpacity = 1.0
u_hasColour = 0
u_attachedCount = 0
u_orthographic = 0
u_viewDirection = [0.0, 0.0, 1.0]
[bindings]
u_modelMatrix = model_matrix
u_viewMatrix = view_matrix
u_projectionMatrix = projection_matrix
u_normalMatrix = normal_matrix
u_viewPosition = view_position
[attributes]
a_vertex = vertex
a_normal = normal
a_color = color
