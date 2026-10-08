[shaders]
vertex =
    uniform mat4 u_modelMatrix;
    uniform mat4 u_viewMatrix;
    uniform mat4 u_projectionMatrix;
    uniform mat4 u_normalMatrix;
    uniform mat4 u_previewRotation;
    uniform int u_previewEnabled;
    uniform int u_previewHighlight;
    uniform float u_previewBody;
    attribute vec3 a_vertex;
    attribute vec3 a_normal;
    attribute vec4 a_color;
    attribute float a_surface;
    attribute vec4 a_material;
    attribute float a_body;
    attribute vec2 a_finish;
    varying vec3 v_normal;
    varying vec3 v_position;
    varying vec4 v_color;
    varying float v_surface;
    varying vec4 v_material;
    varying vec2 v_finish;
    varying vec3 v_localPosition;
    varying vec3 v_localNormal;
    varying float v_body;
    void main() {
        vec3 vertex = a_vertex;
        vec3 normal = a_normal;
        if (u_previewEnabled != 0 && abs(a_body-u_previewBody) < 0.5) {
            vertex = (u_previewRotation * vec4(vertex,1.0)).xyz;
            normal = (u_previewRotation * vec4(normal,0.0)).xyz;
        }
        vec4 world = u_modelMatrix * vec4(vertex, 1.0);
        gl_Position = u_projectionMatrix * u_viewMatrix * world;
        v_position = world.xyz;
        v_normal = (u_normalMatrix * vec4(normal, 0.0)).xyz;
        v_color = a_color;
        if (u_previewHighlight != 0 && abs(a_body-u_previewBody) < 0.5)
            v_color.rgb = mix(v_color.rgb, vec3(0.12,0.72,0.9),0.35);
        v_body = a_body;
        v_surface = a_surface;
        v_material = a_material;
        v_finish = a_finish;
        v_localPosition = a_vertex;
        v_localNormal = a_normal;
    }

fragment =
    uniform float u_opacity;
    uniform float u_surfaceDetail;
    uniform int u_materialOverridesEnabled;
    uniform int u_materialEditEnabled;
    uniform vec4 u_materialRoughness0;
    uniform vec4 u_materialRoughness1;
    uniform vec4 u_materialRoughness2;
    uniform vec4 u_materialRoughness3;
    uniform vec4 u_materialRoughness4;
    uniform vec4 u_materialRoughness5;
    uniform vec4 u_materialReflectivity0;
    uniform vec4 u_materialReflectivity1;
    uniform vec4 u_materialReflectivity2;
    uniform vec4 u_materialReflectivity3;
    uniform vec4 u_materialReflectivity4;
    uniform vec4 u_materialReflectivity5;
    uniform int u_environmentEnabled;
    uniform samplerCube u_environment;
    uniform samplerCube u_sceneDepth;
    uniform vec3 u_probe;
    uniform vec3 u_sceneMin;
    uniform vec3 u_sceneMax;
    uniform float u_probeNear;
    uniform float u_probeFar;
    uniform mat4 u_normalMatrix;
    uniform mat4 u_previewRotation;
    uniform int u_previewEnabled;
    uniform int u_previewHighlight;
    uniform float u_previewBody;
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
    uniform int u_orthographic;
    uniform vec3 u_viewDirection;
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
    varying vec4 v_material;
    varying vec2 v_finish;
    varying vec3 v_localPosition;
    varying vec3 v_localNormal;
    varying float v_body;

    float materialChannel(vec4 bank, float lane) {
        if (lane < 0.5) return bank.x;
        if (lane < 1.5) return bank.y;
        if (lane < 2.5) return bank.z;
        return bank.w;
    }
    vec2 materialFinish() {
        float kind = floor(v_material.w + 0.5);
        float lane = mod(kind, 4.0);
        if (kind < 3.5) return vec2(materialChannel(u_materialRoughness0, lane), materialChannel(u_materialReflectivity0, lane));
        if (kind < 7.5) return vec2(materialChannel(u_materialRoughness1, lane), materialChannel(u_materialReflectivity1, lane));
        if (kind < 11.5) return vec2(materialChannel(u_materialRoughness2, lane), materialChannel(u_materialReflectivity2, lane));
        if (kind < 15.5) return vec2(materialChannel(u_materialRoughness3, lane), materialChannel(u_materialReflectivity3, lane));
        if (kind < 19.5) return vec2(materialChannel(u_materialRoughness4, lane), materialChannel(u_materialReflectivity4, lane));
        if (kind < 23.5) return vec2(materialChannel(u_materialRoughness5, lane), materialChannel(u_materialReflectivity5, lane));
        return vec2(-1.0, -1.0);
    }
    vec3 materialColour() {
        float kind = floor(v_material.w + 0.5);
        if (kind < 0.5) return vec3(0.55);
        if (abs(kind-2.0) < 0.5) return vec3(0.18, 0.55, 1.0);
        if (abs(kind-3.0) < 0.5) return vec3(1.0, 0.7, 0.12);
        if (abs(kind-7.0) < 0.5 || kind >= 19.5) return vec3(0.3, 0.3, 0.35);
        return vec3(1.0);
    }
    float radialResidual(vec3 point) {
        vec3 offset = point - u_probe;
        float radius = length(offset);
        if (!(radius > 0.001 && radius < u_probeFar * 2.0)) return -100000.0;
        vec3 direction = offset / radius;
        float depth = textureCube(u_sceneDepth, direction, 0.0).r;
        if (!(depth >= 0.0 && depth < 1.0)) return -100000.0;
        float forward = 2.0 * u_probeNear * u_probeFar / (u_probeFar + u_probeNear
            - (2.0 * depth - 1.0) * (u_probeFar - u_probeNear));
        float radial = forward / max(max(abs(direction.x), abs(direction.y)), abs(direction.z));
        return radius - radial;
    }
    vec3 localReflectionDirection(vec3 direction) {
        // Bounds restrict the search only; every hit must converge on captured depth.
        vec3 origin = v_position + direction * 0.01;
        vec3 safe = vec3(direction.x < 0.0 ? -1.0 : 1.0, direction.y < 0.0 ? -1.0 : 1.0, direction.z < 0.0 ? -1.0 : 1.0)
                  * max(abs(direction), vec3(0.000001));
        vec3 a = (u_sceneMin - origin) / safe, b = (u_sceneMax - origin) / safe;
        vec3 low = min(a, b), high = max(a, b);
        float first = max(0.0, max(max(low.x, low.y), low.z));
        float last = min(min(high.x, high.y), high.z);
        if (!(last > first && last < u_probeFar * 4.0)) return vec3(0.0);
        float previous = first, value = radialResidual(origin + direction * first);
        int candidates = 0;
        for (int i = 1; i <= 16; ++i) {
            float t = mix(first, last, float(i) / 16.0);
            float sampleValue = radialResidual(origin + direction * t);
            if (value > -99999.0 && sampleValue > -99999.0 && value <= 0.0 && sampleValue >= 0.0) {
                if (candidates >= 2) return vec3(0.0);
                candidates += 1;
                bool valid = true;
                float left = previous, right = t;
                float leftValue = value, rightValue = sampleValue;
                for (int j = 0; j < 6; ++j) {
                    float middle = (left + right) * 0.5;
                    float middleValue = radialResidual(origin + direction * middle);
                    if (!(middleValue > -99999.0)) { valid = false; break; }
                    if (middleValue > 0.0) { right = middle; rightValue = middleValue; }
                    else { left = middle; leftValue = middleValue; }
                }
                vec3 hit = origin + direction * ((left + right) * 0.5);
                float tolerance = max(0.02, length(hit - u_probe) * 0.003);
                // A silhouette jump can bracket zero without a physical intersection.
                if (valid && abs(leftValue) <= tolerance && abs(rightValue) <= tolerance)
                    return normalize(hit - u_probe);
            }
            previous = t; value = sampleValue;
        }
        return vec3(0.0);
    }
    vec3 environmentReflection(vec3 direction, float roughness) {
        direction = localReflectionDirection(direction);
        if (dot(direction, direction) < 0.5) return vec3(0.08, 0.09, 0.11);
        // One central depth traversal, then a bounded roughness cone.
        vec3 tangent = normalize(cross(direction, abs(direction.y) < 0.9 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0)));
        vec3 bitangent = cross(direction, tangent);
        float cone = roughness * roughness * 0.7;
        float level = roughness * 9.0;
        vec3 colour = textureCube(u_environment, direction, level).rgb * 0.4;
        colour += textureCube(u_environment, normalize(direction + tangent * cone), level).rgb * 0.1;
        colour += textureCube(u_environment, normalize(direction - tangent * cone), level).rgb * 0.1;
        colour += textureCube(u_environment, normalize(direction + bitangent * cone), level).rgb * 0.1;
        colour += textureCube(u_environment, normalize(direction - bitangent * cone), level).rgb * 0.1;
        colour += textureCube(u_environment, normalize(direction + (tangent + bitangent) * cone * 0.7), level).rgb * 0.1;
        colour += textureCube(u_environment, normalize(direction - (tangent + bitangent) * cone * 0.7), level).rgb * 0.1;
        return colour;
    }
    vec3 surfaceNormal() {
        // Physical sub-mm grain belongs to the immutable model, not the
        // camera/world. Derivative filtering fades unresolved grain rather
        // than letting it shimmer as the camera or a rotor moves.
        vec3 normal = normalize(v_normal);
        float strength = clamp(u_surfaceDetail, 0.0, 1.0) * v_material.z;
        if (strength <= 0.0) return normal;
        vec3 p = v_localPosition;
        vec3 k0 = vec3(11.3, 17.1, 13.7);
        vec3 k1 = vec3(-19.7, 7.3, 23.9);
        vec3 k2 = vec3(29.1, -21.7, 5.9);
        float footprint = max(length(dFdx(p)), length(dFdy(p))) * 5.9;
        float resolved = 1.0 - smoothstep(0.25, 0.8, footprint);
        vec3 gradient = (cos(dot(p, k0)) * k0 + cos(dot(p, k1)) * k1
                       + cos(dot(p, k2)) * k2) / 90.0;
        if (abs(v_material.w-7.0) < 0.5 || v_material.w >= 19.5) {
            vec3 fibre = vec3(37.0, 5.0, 11.0);
            gradient += fibre * cos(dot(p, fibre)) * sin(dot(p, k1)*0.23) / 90.0;
        }
        vec3 localNormal = normalize(v_localNormal);
        gradient -= localNormal * dot(localNormal, gradient);
        if (u_previewEnabled != 0 && abs(v_body-u_previewBody) < 0.5)
            gradient = (u_previewRotation * vec4(gradient,0.0)).xyz;
        vec3 detail = (u_normalMatrix * vec4(gradient, 0.0)).xyz;
        return normalize(normal + detail * (0.07 * strength * resolved));
    }
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
        if (u_materialEditEnabled == 1) { gl_FragColor = vec4(materialColour(), v_color.a * u_opacity); return; }
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
        vec3 normal = surfaceNormal();
        float diffuse = led(u_light0, u_direction0, normal) + led(u_light1, u_direction1, normal)
                      + led(u_light2, u_direction2, normal) + led(u_light3, u_direction3, normal);
        vec3 eye = normalize(u_orthographic == 1 ? u_viewDirection : u_viewPosition - v_position);
        vec3 halfVector = normalize(normalize(u_light0 - v_position) + eye);
        float roughness = clamp(v_material.x, 0.04, 1.0);
        float known = step(0.5, v_material.w);
        vec2 finish = u_materialOverridesEnabled == 1 ? materialFinish() : vec2(-1.0);
        if (v_finish.x >= 0.0 || v_finish.y >= 0.0) known = 1.0;
        if (v_finish.x >= 0.0) finish.x = v_finish.x;
        if (v_finish.y >= 0.0) finish.y = v_finish.y;
        if (finish.x >= 0.0) { roughness = clamp(finish.x, 0.04, 1.0); known = 1.0; }
        if (finish.y >= 0.0 && v_material.w < 0.5 && abs(finish.y-0.04) > 0.00001) known = 1.0;
        float shininess = mix(24.0, mix(4.0, 128.0, pow(1.0 - roughness, 4.0)), known);
        float specular = pow(max(dot(normal, halfVector), 0.0), shininess);
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
        vec3 f0 = mix(vec3(0.04), baseColour, v_material.y);
        if (finish.y >= 0.0) f0 = mix(vec3(finish.y), baseColour * finish.y, v_material.y);
        vec3 materialLit = baseColour * (0.34 + 0.44 * diffuse) * (1.0 - v_material.y * 0.80)
                         + max(baseColour, vec3(0.035)) * illumination * (1.0 - v_material.y * 0.80)
                         + f0 * (specular * 1.8 + colouredReflection * 3.0) + emission;
        lit = mix(lit, materialLit, known);
        if (u_environmentEnabled == 1) {
            float facing = max(dot(normal, eye), 0.0);
            vec3 fresnel = f0 + (vec3(1.0) - f0) * pow(1.0 - facing, 5.0);
            vec3 reflected = environmentReflection(reflect(-eye, normal), roughness);
            lit = lit * (vec3(1.0) - fresnel * (1.0 - roughness * 0.5))
                + reflected * fresnel * (1.0 - roughness * 0.4);
        }
        gl_FragColor = vec4(lit, v_color.a * u_opacity);
    }

vertex41core =
    #version 410
    uniform mat4 u_modelMatrix;
    uniform mat4 u_viewMatrix;
    uniform mat4 u_projectionMatrix;
    uniform mat4 u_normalMatrix;
    uniform mat4 u_previewRotation;
    uniform int u_previewEnabled;
    uniform int u_previewHighlight;
    uniform float u_previewBody;
    in vec3 a_vertex;
    in vec3 a_normal;
    in vec4 a_color;
    in float a_surface;
    in vec4 a_material;
    in float a_body;
    in vec2 a_finish;
    out vec3 v_normal;
    out vec3 v_position;
    out vec4 v_color;
    out float v_surface;
    out vec4 v_material;
    out vec2 v_finish;
    out vec3 v_localPosition;
    out vec3 v_localNormal;
    out float v_body;
    void main() {
        vec3 vertex = a_vertex;
        vec3 normal = a_normal;
        if (u_previewEnabled != 0 && abs(a_body-u_previewBody) < 0.5) {
            vertex = (u_previewRotation * vec4(vertex,1.0)).xyz;
            normal = (u_previewRotation * vec4(normal,0.0)).xyz;
        }
        vec4 world = u_modelMatrix * vec4(vertex, 1.0);
        gl_Position = u_projectionMatrix * u_viewMatrix * world;
        v_position = world.xyz;
        v_normal = (u_normalMatrix * vec4(normal, 0.0)).xyz;
        v_color = a_color;
        if (u_previewHighlight != 0 && abs(a_body-u_previewBody) < 0.5)
            v_color.rgb = mix(v_color.rgb, vec3(0.12,0.72,0.9),0.35);
        v_body = a_body;
        v_surface = a_surface;
        v_material = a_material;
        v_finish = a_finish;
        v_localPosition = a_vertex;
        v_localNormal = a_normal;
    }

fragment41core =
    #version 410
    uniform float u_opacity;
    uniform float u_surfaceDetail;
    uniform int u_materialOverridesEnabled;
    uniform int u_materialEditEnabled;
    uniform vec4 u_materialRoughness0;
    uniform vec4 u_materialRoughness1;
    uniform vec4 u_materialRoughness2;
    uniform vec4 u_materialRoughness3;
    uniform vec4 u_materialRoughness4;
    uniform vec4 u_materialRoughness5;
    uniform vec4 u_materialReflectivity0;
    uniform vec4 u_materialReflectivity1;
    uniform vec4 u_materialReflectivity2;
    uniform vec4 u_materialReflectivity3;
    uniform vec4 u_materialReflectivity4;
    uniform vec4 u_materialReflectivity5;
    uniform int u_environmentEnabled;
    uniform samplerCube u_environment;
    uniform samplerCube u_sceneDepth;
    uniform vec3 u_probe;
    uniform vec3 u_sceneMin;
    uniform vec3 u_sceneMax;
    uniform float u_probeNear;
    uniform float u_probeFar;
    uniform mat4 u_normalMatrix;
    uniform mat4 u_previewRotation;
    uniform int u_previewEnabled;
    uniform int u_previewHighlight;
    uniform float u_previewBody;
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
    uniform int u_orthographic;
    uniform vec3 u_viewDirection;
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
    in vec4 v_material;
    in vec2 v_finish;
    in vec3 v_localPosition;
    in vec3 v_localNormal;
    in float v_body;
    out vec4 frag_color;
    float materialChannel(vec4 bank, float lane) {
        if (lane < 0.5) return bank.x;
        if (lane < 1.5) return bank.y;
        if (lane < 2.5) return bank.z;
        return bank.w;
    }
    vec2 materialFinish() {
        float kind = floor(v_material.w + 0.5);
        float lane = mod(kind, 4.0);
        if (kind < 3.5) return vec2(materialChannel(u_materialRoughness0, lane), materialChannel(u_materialReflectivity0, lane));
        if (kind < 7.5) return vec2(materialChannel(u_materialRoughness1, lane), materialChannel(u_materialReflectivity1, lane));
        if (kind < 11.5) return vec2(materialChannel(u_materialRoughness2, lane), materialChannel(u_materialReflectivity2, lane));
        if (kind < 15.5) return vec2(materialChannel(u_materialRoughness3, lane), materialChannel(u_materialReflectivity3, lane));
        if (kind < 19.5) return vec2(materialChannel(u_materialRoughness4, lane), materialChannel(u_materialReflectivity4, lane));
        if (kind < 23.5) return vec2(materialChannel(u_materialRoughness5, lane), materialChannel(u_materialReflectivity5, lane));
        return vec2(-1.0, -1.0);
    }
    vec3 materialColour() {
        float kind = floor(v_material.w + 0.5);
        if (kind < 0.5) return vec3(0.55);
        if (abs(kind-2.0) < 0.5) return vec3(0.18, 0.55, 1.0);
        if (abs(kind-3.0) < 0.5) return vec3(1.0, 0.7, 0.12);
        if (abs(kind-7.0) < 0.5 || kind >= 19.5) return vec3(0.3, 0.3, 0.35);
        return vec3(1.0);
    }
    float radialResidual(vec3 point) {
        vec3 offset = point - u_probe;
        float radius = length(offset);
        if (!(radius > 0.001 && radius < u_probeFar * 2.0)) return -100000.0;
        vec3 direction = offset / radius;
        float depth = textureLod(u_sceneDepth, direction, 0.0).r;
        if (!(depth >= 0.0 && depth < 1.0)) return -100000.0;
        float forward = 2.0 * u_probeNear * u_probeFar / (u_probeFar + u_probeNear
            - (2.0 * depth - 1.0) * (u_probeFar - u_probeNear));
        float radial = forward / max(max(abs(direction.x), abs(direction.y)), abs(direction.z));
        return radius - radial;
    }
    vec3 localReflectionDirection(vec3 direction) {
        // Bounds restrict the search only; every hit must converge on captured depth.
        vec3 origin = v_position + direction * 0.01;
        vec3 safe = vec3(direction.x < 0.0 ? -1.0 : 1.0, direction.y < 0.0 ? -1.0 : 1.0, direction.z < 0.0 ? -1.0 : 1.0)
                  * max(abs(direction), vec3(0.000001));
        vec3 a = (u_sceneMin - origin) / safe, b = (u_sceneMax - origin) / safe;
        vec3 low = min(a, b), high = max(a, b);
        float first = max(0.0, max(max(low.x, low.y), low.z));
        float last = min(min(high.x, high.y), high.z);
        if (!(last > first && last < u_probeFar * 4.0)) return vec3(0.0);
        float previous = first, value = radialResidual(origin + direction * first);
        int candidates = 0;
        for (int i = 1; i <= 16; ++i) {
            float t = mix(first, last, float(i) / 16.0);
            float sampleValue = radialResidual(origin + direction * t);
            if (value > -99999.0 && sampleValue > -99999.0 && value <= 0.0 && sampleValue >= 0.0) {
                if (candidates >= 2) return vec3(0.0);
                candidates += 1;
                bool valid = true;
                float left = previous, right = t;
                float leftValue = value, rightValue = sampleValue;
                for (int j = 0; j < 6; ++j) {
                    float middle = (left + right) * 0.5;
                    float middleValue = radialResidual(origin + direction * middle);
                    if (!(middleValue > -99999.0)) { valid = false; break; }
                    if (middleValue > 0.0) { right = middle; rightValue = middleValue; }
                    else { left = middle; leftValue = middleValue; }
                }
                vec3 hit = origin + direction * ((left + right) * 0.5);
                float tolerance = max(0.02, length(hit - u_probe) * 0.003);
                // A silhouette jump can bracket zero without a physical intersection.
                if (valid && abs(leftValue) <= tolerance && abs(rightValue) <= tolerance)
                    return normalize(hit - u_probe);
            }
            previous = t; value = sampleValue;
        }
        return vec3(0.0);
    }
    vec3 environmentReflection(vec3 direction, float roughness) {
        direction = localReflectionDirection(direction);
        if (dot(direction, direction) < 0.5) return vec3(0.08, 0.09, 0.11);
        // One central depth traversal, then a bounded roughness cone.
        vec3 tangent = normalize(cross(direction, abs(direction.y) < 0.9 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0)));
        vec3 bitangent = cross(direction, tangent);
        float cone = roughness * roughness * 0.7;
        float level = roughness * 9.0;
        vec3 colour = textureLod(u_environment, direction, level).rgb * 0.4;
        colour += textureLod(u_environment, normalize(direction + tangent * cone), level).rgb * 0.1;
        colour += textureLod(u_environment, normalize(direction - tangent * cone), level).rgb * 0.1;
        colour += textureLod(u_environment, normalize(direction + bitangent * cone), level).rgb * 0.1;
        colour += textureLod(u_environment, normalize(direction - bitangent * cone), level).rgb * 0.1;
        colour += textureLod(u_environment, normalize(direction + (tangent + bitangent) * cone * 0.7), level).rgb * 0.1;
        colour += textureLod(u_environment, normalize(direction - (tangent + bitangent) * cone * 0.7), level).rgb * 0.1;
        return colour;
    }
    vec3 surfaceNormal() {
        // Physical sub-mm grain belongs to the immutable model, not the
        // camera/world. Derivative filtering fades unresolved grain rather
        // than letting it shimmer as the camera or a rotor moves.
        vec3 normal = normalize(v_normal);
        float strength = clamp(u_surfaceDetail, 0.0, 1.0) * v_material.z;
        if (strength <= 0.0) return normal;
        vec3 p = v_localPosition;
        vec3 k0 = vec3(11.3, 17.1, 13.7);
        vec3 k1 = vec3(-19.7, 7.3, 23.9);
        vec3 k2 = vec3(29.1, -21.7, 5.9);
        float footprint = max(length(dFdx(p)), length(dFdy(p))) * 5.9;
        float resolved = 1.0 - smoothstep(0.25, 0.8, footprint);
        vec3 gradient = (cos(dot(p, k0)) * k0 + cos(dot(p, k1)) * k1
                       + cos(dot(p, k2)) * k2) / 90.0;
        if (abs(v_material.w-7.0) < 0.5 || v_material.w >= 19.5) {
            vec3 fibre = vec3(37.0, 5.0, 11.0);
            gradient += fibre * cos(dot(p, fibre)) * sin(dot(p, k1)*0.23) / 90.0;
        }
        vec3 localNormal = normalize(v_localNormal);
        gradient -= localNormal * dot(localNormal, gradient);
        if (u_previewEnabled != 0 && abs(v_body-u_previewBody) < 0.5)
            gradient = (u_previewRotation * vec4(gradient,0.0)).xyz;
        vec3 detail = (u_normalMatrix * vec4(gradient, 0.0)).xyz;
        return normalize(normal + detail * (0.07 * strength * resolved));
    }
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
        if (u_materialEditEnabled == 1) { frag_color = vec4(materialColour(), v_color.a * u_opacity); return; }
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
        vec3 normal = surfaceNormal();
        float diffuse = led(u_light0, u_direction0, normal) + led(u_light1, u_direction1, normal)
                      + led(u_light2, u_direction2, normal) + led(u_light3, u_direction3, normal);
        vec3 eye = normalize(u_orthographic == 1 ? u_viewDirection : u_viewPosition - v_position);
        vec3 halfVector = normalize(normalize(u_light0 - v_position) + eye);
        float roughness = clamp(v_material.x, 0.04, 1.0);
        float known = step(0.5, v_material.w);
        vec2 finish = u_materialOverridesEnabled == 1 ? materialFinish() : vec2(-1.0);
        if (v_finish.x >= 0.0 || v_finish.y >= 0.0) known = 1.0;
        if (v_finish.x >= 0.0) finish.x = v_finish.x;
        if (v_finish.y >= 0.0) finish.y = v_finish.y;
        if (finish.x >= 0.0) { roughness = clamp(finish.x, 0.04, 1.0); known = 1.0; }
        if (finish.y >= 0.0 && v_material.w < 0.5 && abs(finish.y-0.04) > 0.00001) known = 1.0;
        float shininess = mix(24.0, mix(4.0, 128.0, pow(1.0 - roughness, 4.0)), known);
        float specular = pow(max(dot(normal, halfVector), 0.0), shininess);
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
        vec3 f0 = mix(vec3(0.04), baseColour, v_material.y);
        if (finish.y >= 0.0) f0 = mix(vec3(finish.y), baseColour * finish.y, v_material.y);
        vec3 materialLit = baseColour * (0.34 + 0.44 * diffuse) * (1.0 - v_material.y * 0.80)
                         + max(baseColour, vec3(0.035)) * illumination * (1.0 - v_material.y * 0.80)
                         + f0 * (specular * 1.8 + colouredReflection * 3.0) + emission;
        lit = mix(lit, materialLit, known);
        if (u_environmentEnabled == 1) {
            float facing = max(dot(normal, eye), 0.0);
            vec3 fresnel = f0 + (vec3(1.0) - f0) * pow(1.0 - facing, 5.0);
            vec3 reflected = environmentReflection(reflect(-eye, normal), roughness);
            lit = lit * (vec3(1.0) - fresnel * (1.0 - roughness * 0.5))
                + reflected * fresnel * (1.0 - roughness * 0.4);
        }
        frag_color = vec4(lit, v_color.a * u_opacity);
    }

[defaults]
u_orthographic = 0
u_viewDirection = 0, 0, 1
u_environmentEnabled = 0
u_environment = 7
u_sceneDepth = 6
u_probe = [0.0, 0.0, 0.0]
u_sceneMin = [-1.0, -1.0, -1.0]
u_sceneMax = [1.0, 1.0, 1.0]
u_probeNear = 0.2
u_probeFar = 1000.0
u_surfaceDetail = 0.35
u_opacity = 1.0
u_depthOnly = 0
u_lightingEnabled = 1
u_attachedCount = 0
u_previewEnabled = 0
u_previewHighlight = 0
u_previewBody = -1.0

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
