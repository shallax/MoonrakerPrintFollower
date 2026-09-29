#version 440
layout(location=0) in vec2 position;
layout(location=1) in vec2 direction;
layout(location=2) in vec2 corner;
layout(location=3) in vec2 motion;
layout(location=4) in vec2 metrics;
// All vertices of a stroke carry the same ink, width and visibility.
// Ordinary varyings preserve those constants and work on GLSL 120 / ES 100;
// flat interpolation qualifiers are unavailable on those legacy targets.
layout(location=0) out vec2 localPos;
layout(location=1) out float segmentLength;
layout(location=2) out float visibleSegment;
layout(location=3) out float halfWidth;
layout(std140,binding=0) uniform buf { mat4 matrix; vec4 colour; vec4 parameters; vec4 options; vec4 colourOptions; vec4 palette[16]; } ubuf;
layout(location=4) out vec4 ink;
vec3 gradient(float mode, float value) {
    float lo = ubuf.colourOptions.y, hi = ubuf.colourOptions.z;
    bool constantRange = abs(hi-lo) < .0001;
    float v = constantRange ? .5 : clamp((value-lo)/(hi-lo),0.,1.);
    if (mode == 3.) return vec3(clamp(4.*v-2.,0.,1.), v>.75?v:min(1.5*v,.75), .75-abs(.25-v));
    if (mode == 5.) {
        float t = constantRange ? 0. : 2.*v-1.;
        return clamp(vec3(1.5)-abs(vec3(2.*t-1.,2.*t,2.*t+1.)),0.,1.);
    }
    return vec3(v,v>.375?.5:1.-abs(1.-4.*v),max(1.-4.*v,0.));
}
void main() {
    float mode = ubuf.colourOptions.x;
    ink = ubuf.colour;
    if (mode == 0.) {
        vec4 material = ubuf.palette[int(clamp(metrics.y,0.,15.))];
        ink = vec4(material.rgb * material.a,material.a) * ubuf.colour.a;
    } else if (mode >= 2.) {
        float width = abs(corner.x), height = ubuf.colourOptions.w;
        float value = mode == 2. ? metrics.x : (mode == 3. ? height : (mode == 4. ? width : width*height*metrics.x));
        ink = vec4(gradient(mode,value),1.) * ubuf.colour.a;
    }
    float amount = ubuf.options.z > 0.5 ? clamp((ubuf.options.y - motion.x) / max(motion.y,0.000001),0.0,1.0) : 1.0;
    visibleSegment = amount;
    vec2 shortened = direction * amount;
    vec2 point = position - (corner.x > 0.0 ? direction - shortened : vec2(0));
    vec4 anchor = ubuf.matrix * vec4(point,0,1);
    vec4 other = ubuf.matrix * vec4(point + shortened,0,1);
    vec2 delta = (other.xy / other.w - anchor.xy / anchor.w) * ubuf.parameters.yz;
    segmentLength = length(delta);
    vec2 tangent = segmentLength > 0.0001 ? delta / segmentLength : vec2(1,0);
    vec2 normal = vec2(-tangent.y,tangent.x);
    halfWidth = ubuf.parameters.x * (ubuf.options.w > 0.5 ? abs(corner.x) : 1.0);
    float endSign = sign(corner.x);
    float radius = halfWidth + (ubuf.parameters.w > 0.5 ? 1.0 : 0.0);
    vec2 offset = (tangent * endSign + normal * corner.y) * radius;
    gl_Position = anchor + vec4(offset / ubuf.parameters.yz * anchor.w,0,0);
    localPos = vec2((corner.x < 0 ? 0 : segmentLength) + endSign*radius, corner.y*radius);
}
