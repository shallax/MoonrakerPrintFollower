[shaders]
vertex =
    uniform mat4 u_modelMatrix;
    uniform mat4 u_viewMatrix;
    uniform mat4 u_projectionMatrix;
    uniform mat4 u_normalMatrix;
    attribute vec3 a_vertex;
    attribute vec3 a_normal;
    attribute vec4 a_color;
    attribute float a_surface;
    varying vec3 v_normal;
    varying vec3 v_position;
    varying vec4 v_color;
    varying float v_surface;
    void main() {
        vec4 world = u_modelMatrix * vec4(a_vertex, 1.0);
        gl_Position = u_projectionMatrix * u_viewMatrix * world;
        v_position = world.xyz;
        v_normal = (u_normalMatrix * vec4(a_normal, 0.0)).xyz;
        v_color = a_color;
        v_surface = a_surface;
    }

fragment =
    uniform float u_opacity;
    uniform int u_depthOnly;
    uniform int u_lightingEnabled;
    uniform int u_attachedCount;
    uniform vec3 u_attachedPosition[8];
    uniform vec3 u_attachedDirection[8];
    uniform vec3 u_attachedColour[8];
    uniform float u_attachedRange[8];
    uniform float u_attachedSurface[8];
    uniform vec4 u_attachedPaint[8];
    uniform vec3 u_viewPosition;
    uniform vec3 u_light0;
    uniform vec3 u_light1;
    uniform vec3 u_light2;
    uniform vec3 u_light3;
    uniform vec3 u_direction0;
    uniform vec3 u_direction1;
    uniform vec3 u_direction2;
    uniform vec3 u_direction3;
    varying vec3 v_normal;
    varying vec3 v_position;
    varying vec4 v_color;
    varying float v_surface;

    float led(vec3 position, vec3 direction, vec3 normal) {
        vec3 toLight = normalize(position - v_position);
        // Broad strip emission, aimed 45 degrees inward/down. Four white
        // sources preserve the CAD palette and illuminate all bed quadrants.
        float emission = max(dot(-toLight, normalize(direction)), 0.0);
        return max(dot(normal, toLight), 0.0) * emission;
    }
    void main() {
        if (v_color.a <= 0.0) discard;
        if (u_depthOnly == 1) { gl_FragColor = vec4(0.0); return; }
        if (u_lightingEnabled == 0) {
            vec3 colour = v_color.rgb;
            for (int i = 0; i < 8; ++i) {
                if (i >= u_attachedCount) break;
                if (u_attachedPaint[i].a > 0.5 && abs(v_surface - u_attachedSurface[i]) < 0.25)
                    colour = u_attachedPaint[i].rgb;
            }
            gl_FragColor = vec4(colour, v_color.a * u_opacity);
            return;
        }
        vec3 normal = normalize(v_normal);
        float diffuse = led(u_light0, u_direction0, normal) + led(u_light1, u_direction1, normal)
                      + led(u_light2, u_direction2, normal) + led(u_light3, u_direction3, normal);
        vec3 eye = normalize(u_viewPosition - v_position);
        vec3 halfVector = normalize(normalize(u_light0 - v_position) + eye);
        float specular = pow(max(dot(normal, halfVector), 0.0), 24.0);
        vec3 illumination = vec3(0.0);
        vec3 colouredReflection = vec3(0.0);
        vec3 baseColour = v_color.rgb;
        vec3 emission = vec3(0.0);
        for (int i = 0; i < 8; ++i) {
            if (i >= u_attachedCount) break;
            if (u_attachedPaint[i].a > 0.5 && abs(v_surface - u_attachedSurface[i]) < 0.25) {
                baseColour = u_attachedPaint[i].rgb;
                emission = u_attachedColour[i] * 0.35;
            }
            // Paint and emission above belong to the selected surface even
            // when this light cannot illuminate the current fragment.
            if (dot(u_attachedColour[i], u_attachedColour[i]) <= 0.0) continue;
            vec3 delta = u_attachedPosition[i] - v_position;
            float squaredDistance = dot(delta, delta);
            if (squaredDistance >= u_attachedRange[i] * u_attachedRange[i]) continue;
            float distanceToLight = max(sqrt(squaredDistance), 0.001);
            vec3 toLight = delta / distanceToLight;
            // Never emit behind the selected face's outward normal.
            float outward = max(dot(normalize(u_attachedDirection[i]), -toLight), 0.0);
            if (outward <= 0.0) continue;
            float falloff = max(1.0 - distanceToLight/u_attachedRange[i], 0.0);
            float facing = max(dot(normal, toLight), 0.0);
            // Broad outward emission and dielectric highlights: plastic's
            // specular reflection carries the light colour independently of
            // its pigment. Keep a small diffuse floor for idealised CAD RGB.
            float energy = outward * falloff*falloff;
            illumination += u_attachedColour[i] * energy * facing * 3.0;
            vec3 halfLight = normalize(toLight + eye);
            float broadHighlight = pow(max(dot(normal, halfLight), 0.0), 8.0);
            colouredReflection += u_attachedColour[i] * energy * broadHighlight * 0.30;
        }
        vec3 lit = baseColour * (0.34 + 0.44 * diffuse)
                 + max(baseColour, vec3(0.035)) * illumination
                 + colouredReflection + emission + vec3(0.08 * specular);
        gl_FragColor = vec4(lit, v_color.a * u_opacity);
    }

vertex41core =
    #version 410
    uniform mat4 u_modelMatrix;
    uniform mat4 u_viewMatrix;
    uniform mat4 u_projectionMatrix;
    uniform mat4 u_normalMatrix;
    in vec3 a_vertex;
    in vec3 a_normal;
    in vec4 a_color;
    in float a_surface;
    out vec3 v_normal;
    out vec3 v_position;
    out vec4 v_color;
    out float v_surface;
    void main() {
        vec4 world = u_modelMatrix * vec4(a_vertex, 1.0);
        gl_Position = u_projectionMatrix * u_viewMatrix * world;
        v_position = world.xyz;
        v_normal = (u_normalMatrix * vec4(a_normal, 0.0)).xyz;
        v_color = a_color;
        v_surface = a_surface;
    }

fragment41core =
    #version 410
    uniform float u_opacity;
    uniform int u_depthOnly;
    uniform int u_lightingEnabled;
    uniform int u_attachedCount;
    uniform vec3 u_attachedPosition[8];
    uniform vec3 u_attachedDirection[8];
    uniform vec3 u_attachedColour[8];
    uniform float u_attachedRange[8];
    uniform float u_attachedSurface[8];
    uniform vec4 u_attachedPaint[8];
    uniform vec3 u_viewPosition;
    uniform vec3 u_light0;
    uniform vec3 u_light1;
    uniform vec3 u_light2;
    uniform vec3 u_light3;
    uniform vec3 u_direction0;
    uniform vec3 u_direction1;
    uniform vec3 u_direction2;
    uniform vec3 u_direction3;
    in vec3 v_normal;
    in vec3 v_position;
    in vec4 v_color;
    in float v_surface;
    out vec4 frag_color;
    float led(vec3 position, vec3 direction, vec3 normal) {
        vec3 toLight = normalize(position - v_position);
        // Broad strip emission, aimed 45 degrees inward/down. Four white
        // sources preserve the CAD palette and illuminate all bed quadrants.
        float emission = max(dot(-toLight, normalize(direction)), 0.0);
        return max(dot(normal, toLight), 0.0) * emission;
    }
    void main() {
        if (v_color.a <= 0.0) discard;
        if (u_depthOnly == 1) { frag_color = vec4(0.0); return; }
        if (u_lightingEnabled == 0) {
            vec3 colour = v_color.rgb;
            for (int i = 0; i < 8; ++i) {
                if (i >= u_attachedCount) break;
                if (u_attachedPaint[i].a > 0.5 && abs(v_surface - u_attachedSurface[i]) < 0.25)
                    colour = u_attachedPaint[i].rgb;
            }
            frag_color = vec4(colour, v_color.a * u_opacity);
            return;
        }
        vec3 normal = normalize(v_normal);
        float diffuse = led(u_light0, u_direction0, normal) + led(u_light1, u_direction1, normal)
                      + led(u_light2, u_direction2, normal) + led(u_light3, u_direction3, normal);
        vec3 eye = normalize(u_viewPosition - v_position);
        vec3 halfVector = normalize(normalize(u_light0 - v_position) + eye);
        float specular = pow(max(dot(normal, halfVector), 0.0), 24.0);
        vec3 illumination = vec3(0.0);
        vec3 colouredReflection = vec3(0.0);
        vec3 baseColour = v_color.rgb;
        vec3 emission = vec3(0.0);
        for (int i = 0; i < 8; ++i) {
            if (i >= u_attachedCount) break;
            if (u_attachedPaint[i].a > 0.5 && abs(v_surface - u_attachedSurface[i]) < 0.25) {
                baseColour = u_attachedPaint[i].rgb;
                emission = u_attachedColour[i] * 0.35;
            }
            // Paint and emission above belong to the selected surface even
            // when this light cannot illuminate the current fragment.
            if (dot(u_attachedColour[i], u_attachedColour[i]) <= 0.0) continue;
            vec3 delta = u_attachedPosition[i] - v_position;
            float squaredDistance = dot(delta, delta);
            if (squaredDistance >= u_attachedRange[i] * u_attachedRange[i]) continue;
            float distanceToLight = max(sqrt(squaredDistance), 0.001);
            vec3 toLight = delta / distanceToLight;
            // Never emit behind the selected face's outward normal.
            float outward = max(dot(normalize(u_attachedDirection[i]), -toLight), 0.0);
            if (outward <= 0.0) continue;
            float falloff = max(1.0 - distanceToLight/u_attachedRange[i], 0.0);
            float facing = max(dot(normal, toLight), 0.0);
            // Broad outward emission and dielectric highlights: plastic's
            // specular reflection carries the light colour independently of
            // its pigment. Keep a small diffuse floor for idealised CAD RGB.
            float energy = outward * falloff*falloff;
            illumination += u_attachedColour[i] * energy * facing * 3.0;
            vec3 halfLight = normalize(toLight + eye);
            float broadHighlight = pow(max(dot(normal, halfLight), 0.0), 8.0);
            colouredReflection += u_attachedColour[i] * energy * broadHighlight * 0.30;
        }
        vec3 lit = baseColour * (0.34 + 0.44 * diffuse)
                 + max(baseColour, vec3(0.035)) * illumination
                 + colouredReflection + emission + vec3(0.08 * specular);
        frag_color = vec4(lit, v_color.a * u_opacity);
    }

[defaults]
u_opacity = 1.0
u_depthOnly = 0
u_lightingEnabled = 1
u_attachedCount = 0

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
