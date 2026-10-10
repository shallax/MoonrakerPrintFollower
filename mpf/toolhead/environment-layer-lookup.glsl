// Complete original sample-plane CSR values; original blend order is unchanged.
uniform usamplerBuffer mpf_layerRanges,mpf_layerIdentities;
uniform samplerBuffer mpf_layerColours;
uniform ivec2 mpf_layerLookupOrigin,mpf_layerLookupSize;
uniform int mpf_layerLookupEnabled,mpf_layerPixelCount,mpf_layerRecordCount;
uniform int mpf_layerDrawBase,mpf_layerDrawCount;
uniform int mpf_layerSampleCount,mpf_layerLookupPlane;
vec3 environmentReflection(vec3 direction,float roughness,out float confidence){
 if(mpf_layerLookupEnabled==1&&(mpf_layerSampleCount==1||mpf_layerSampleCount==4)
    &&mpf_layerLookupPlane>=0&&mpf_layerLookupPlane<mpf_layerSampleCount
    &&mpf_layerPixelCount>0&&mpf_layerPixelCount%mpf_layerSampleCount==0&&mpf_layerRecordCount>0
    &&mpf_layerDrawCount>0&&mpf_layerDrawCount<=16777216&&mpf_layerDrawBase>=0
    &&mpf_layerDrawBase<=16777216-mpf_layerDrawCount
    &&gl_PrimitiveID>=0&&gl_PrimitiveID<mpf_layerDrawCount){
  ivec2 size=mpf_layerLookupSize;
  int planePixels=mpf_layerPixelCount/mpf_layerSampleCount;
  if(size.x>0&&size.y>0&&size.x<=planePixels/size.y){
   ivec2 pixel=ivec2(floor(gl_FragCoord.xy))-mpf_layerLookupOrigin;
   if(size.x*size.y==planePixels&&all(greaterThanEqual(pixel,ivec2(0)))
      &&all(lessThan(pixel,size))&&textureSize(mpf_layerRanges)==mpf_layerPixelCount
      &&textureSize(mpf_layerIdentities)==mpf_layerRecordCount
      &&textureSize(mpf_layerColours)==mpf_layerRecordCount){
    uvec2 span=texelFetch(mpf_layerRanges,mpf_layerLookupPlane*planePixels+pixel.y*size.x+pixel.x).rg;
    uint total=uint(mpf_layerRecordCount);
    if(span.x<=total&&span.y<=total-span.x){
     uint first=span.x,last=span.x+span.y;
     uint wanted=uint(mpf_layerDrawBase+gl_PrimitiveID);
     // At most31 signed-address bits; no receiver is omitted by a layer cap.
     for(int iteration=0;iteration<32&&first<last;++iteration){
      uint middle=first+(last-first)/2u;
      uint identity=texelFetch(mpf_layerIdentities,int(middle)).r;
      if(identity==wanted){
       vec4 colour=texelFetch(mpf_layerColours,int(middle));
       if(colour.a==1.&&!any(isnan(colour.rgb))&&!any(isinf(colour.rgb))){
        confidence=1.;return colour.rgb;
       }
       return mapEnvironmentReflection(direction,roughness,confidence);
      }
      if(identity<wanted)first=middle+1u;else last=middle;
     }
    }
   }
  }
 }
 return mapEnvironmentReflection(direction,roughness,confidence);
}
