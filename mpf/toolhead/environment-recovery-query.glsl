// Experimental opaque/S1 pass. Tiling changes scissor only, never viewport.
void main(){
 mpf_recoveredColour=vec4(0.);
 ivec2 pixel=ivec2(floor(gl_FragCoord.xy));
 ivec2 size=textureSize(mpf_receiverOriginTexture,0);
 if(any(lessThan(pixel,ivec2(0)))||any(greaterThanEqual(pixel,size))
    ||any(notEqual(size,textureSize(mpf_receiverRayTexture,0))))return;
 vec4 origin=texelFetch(mpf_receiverOriginTexture,pixel,0);
 vec4 ray=texelFetch(mpf_receiverRayTexture,pixel,0);
 if(origin.w!=1.||!mpf_path_finite(origin.xyz)||!mpf_path_finite(ray.xyz)
    ||dot(ray.xyz,ray.xyz)<.5||isnan(ray.w)||isinf(ray.w)||ray.w<.04||ray.w>1.
    ||isnan(u_probeFar)||isinf(u_probeFar)||u_probeFar<=0.)return;
 v_position=origin.xyz;
 float confidence=0.;
 vec3 radiance=environmentReflection(ray.xyz,ray.w,confidence);
 if(confidence==1.&&mpf_path_finite(radiance))mpf_recoveredColour=vec4(radiance,1.);
}
