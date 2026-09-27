#version 440
layout(location=0) in vec2 position;
layout(location=1) in vec2 direction;
layout(location=2) in vec2 corner;
layout(location=0) out vec2 localPos;
layout(location=1) out float segmentLength;
layout(std140,binding=0) uniform buf { mat4 matrix; vec4 colour; vec4 parameters; vec4 options; } ubuf;
void main() {
    vec4 anchor = ubuf.matrix * vec4(position,0,1);
    vec4 other = ubuf.matrix * vec4(position + direction,0,1);
    vec2 delta = (other.xy / other.w - anchor.xy / anchor.w) * ubuf.parameters.yz;
    segmentLength = length(delta);
    vec2 tangent = segmentLength > 0.0001 ? delta / segmentLength : vec2(1,0);
    vec2 normal = vec2(-tangent.y,tangent.x);
    float radius = ubuf.parameters.x + (ubuf.parameters.w > 0.5 ? 1.0 : 0.0);
    vec2 offset = (tangent * corner.x + normal * corner.y) * radius;
    gl_Position = anchor + vec4(offset / ubuf.parameters.yz * anchor.w,0,0);
    localPos = vec2((corner.x < 0 ? 0 : segmentLength) + corner.x*radius, corner.y*radius);
}
