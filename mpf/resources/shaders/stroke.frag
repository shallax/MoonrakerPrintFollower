#version 440
// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-27 [df1d025] Keep stroke shaders compatible with legacy GL and verify native ink
// 2026-09-27 [dd99358] Mirror Cura preview colours and travel states and composite ghost opacity on GPU
// 2026-09-27 [43ab36d] Accelerate follower rendering with retained GPU shader geometry

layout(location=0) in vec2 localPos;
layout(location=1) in float segmentLength;
layout(location=2) in float visibleSegment;
layout(location=3) in float halfWidth;
layout(location=4) in vec4 ink;
layout(location=0) out vec4 fragColor;
layout(std140,binding=0) uniform buf { mat4 matrix; vec4 colour; vec4 parameters; vec4 options; vec4 colourOptions; vec4 palette[16]; } ubuf;
void main() {
    if (visibleSegment <= 0.0) discard;
    float outside = localPos.x - clamp(localPos.x,0.0,segmentLength);
    float distance = ubuf.options.x > 0.5 ? length(vec2(outside,localPos.y)) - halfWidth : max(abs(localPos.y)-halfWidth,max(-localPos.x,localPos.x-segmentLength));
    float coverage = ubuf.parameters.w > 0.5 ? 1.0 - smoothstep(-0.5,0.5,distance) : (distance <= 0 ? 1.0 : 0.0);
    if (coverage <= 0.0) discard;
    fragColor = ink * coverage;
}
