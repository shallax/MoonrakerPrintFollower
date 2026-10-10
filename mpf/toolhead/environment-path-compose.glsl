// Reconstruct normalized RGBA8 storage, not a float base + light sum.
// Native fixed-buffer rounding/dither can differ by one code value; this
// explicit conversion policy requires its own context qualification.
// Exact integer conversion of the incoming FLOAT32 value to nearest-even
// UNORM8. float multiplication by255 can round a value below the half-code
// boundary onto the tie itself (.9f is a native alpha example). No FP64 path.
float mpf_path_unorm8(float value){
 uint bits=floatBitsToUint(clamp(value,0.,1.));
 uint exponent=(bits>>23u)&255u;
 if(exponent<118u)return 0.;
 uint product=((bits&0x7fffffu)|0x800000u)*255u;
 if(exponent==118u)return product>0x80000000u?1./255.:0.;
 uint shift=150u-exponent;
 uint code=product>>shift;
 uint remainder=product&((1u<<shift)-1u),halfCode=1u<<(shift-1u);
 if(remainder>halfCode||(remainder==halfCode&&(code&1u)!=0u))++code;
 return float(code)/255.;
}
vec4 mpf_path_store(vec4 raw){
 if(!mpf_path_finite4(raw)){complete=false;return vec4(0.);}
 return vec4(mpf_path_unorm8(raw.r),mpf_path_unorm8(raw.g),mpf_path_unorm8(raw.b),mpf_path_unorm8(raw.a));
}
vec4 mpf_path_resolved_hit(MPFPathHit hit){
 vec4 stored=mpf_path_store(mpf_path_shade_hit(hit));
 if(!complete)return vec4(0.);
 if(mpf_pathLightModels<0||mpf_pathLightModels>1||mpf_pathAttachedCount<0||mpf_pathAttachedCount>8){
  complete=false;return vec4(0.);
 }
 if(mpf_pathLightModels==0||mpf_pathAttachedCount==0)return stored;
 if(mpf_pathCollectLight!=1||mpf_pathLightHitCount<1||mpf_pathLightHitCount>8){
  complete=false;return vec4(0.);
 }
 bool found=false;int previous=-1;
 for(int i=0;i<mpf_pathLightHitCount&&complete;++i){
  MPFPathHit source=mpf_pathLightHits[i];
  if(source.distance!=hit.distance||source.line<=previous){complete=false;break;}
  previous=source.line;found=found||source.line==hit.line;
  vec4 raw=mpf_path_light_hit(source);
  if(!complete||!mpf_path_finite4(raw)){complete=false;break;}
  // Source components are CLAMPED but not quantized before blending. The
  // native additive source has no intervening normalized attachment. Only
  // the completed framebuffer result is stored after EACH original draw.
  vec3 blended=stored.rgb+clamp(raw.rgb,0.,1.)*clamp(raw.a,0.,1.);
  stored=mpf_path_store(vec4(blended,stored.a));
 }
 if(!found)complete=false;
 return complete?stored:vec4(0.);
}
