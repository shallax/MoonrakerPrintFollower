// Experimental completed opaque/S1 image, exact current cohort/crop only.
uniform sampler2D mpf_recoveredTexture;
uniform int mpf_lookupEnabled;
uniform ivec2 mpf_lookupOrigin,mpf_lookupSize;
vec3 environmentReflection(vec3 direction,float roughness,out float confidence){
 if(mpf_lookupEnabled==1){
  ivec2 pixel=ivec2(floor(gl_FragCoord.xy))-mpf_lookupOrigin;
  ivec2 size=textureSize(mpf_recoveredTexture,0);
  if(all(equal(size,mpf_lookupSize))&&all(greaterThanEqual(pixel,ivec2(0)))
     &&all(lessThan(pixel,size))){
   vec4 colour=texelFetch(mpf_recoveredTexture,pixel,0);
   if(colour.a==1.&&!any(isnan(colour.rgb))&&!any(isinf(colour.rgb))){
    confidence=1.;return colour.rgb;
   }
  }
 }
 return mapEnvironmentReflection(direction,roughness,confidence);
}
