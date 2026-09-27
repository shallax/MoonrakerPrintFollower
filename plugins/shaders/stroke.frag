#version 440
layout(location=0) in vec2 localPos;
layout(location=1) in float segmentLength;
layout(location=0) out vec4 fragColor;
layout(std140,binding=0) uniform buf { mat4 matrix; vec4 colour; vec4 parameters; vec4 options; } ubuf;
void main() {
    float outside = localPos.x - clamp(localPos.x,0.0,segmentLength);
    float distance = ubuf.options.x > 0.5 ? length(vec2(outside,localPos.y)) - ubuf.parameters.x : max(abs(localPos.y)-ubuf.parameters.x,max(-localPos.x,localPos.x-segmentLength));
    float coverage = ubuf.parameters.w > 0.5 ? 1.0 - smoothstep(-0.5,0.5,distance) : (distance <= 0 ? 1.0 : 0.0);
    if (coverage <= 0.0) discard;
    fragColor = ubuf.colour * coverage;
}
