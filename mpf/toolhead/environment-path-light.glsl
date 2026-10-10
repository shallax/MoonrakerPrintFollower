// Raw attached-light source pass for a recovered native path. Include after
// query/source/shade. The owner separately reproduces base RGBA8 storage and
// SRC_ALPHA/ONE RGB blend with ZERO/ONE alpha. This is NOT their float sum.
// A cohort certificate must cover ALL historical partial aliases and extra
// historical start primitives, not just the winning line. Default0 refuses.
uniform int mpf_pathLightModels,mpf_pathLightTop,mpf_pathLightCertified;
uniform int mpf_pathAttachedCount,mpf_pathLightOrthographic;
uniform vec3 mpf_pathLightViewDirection;
uniform vec3 mpf_pathAttachedPosition[8],mpf_pathAttachedDirection[8],mpf_pathAttachedColour[8];
uniform float mpf_pathAttachedRange[8],mpf_pathLightOpacity;

vec3 mpf_path_light_surface(vec3 position, vec3 surfaceNormal, vec3 baseColour) {
 vec3 normal = normalize(surfaceNormal);
 vec3 eye = mpf_pathLightOrthographic == 1 ? normalize(mpf_pathLightViewDirection) : normalize(mpf_pathProbe - position);
 vec3 result = vec3(0.0);
 for (int i = 0; i < 8; ++i) {
  if (i >= mpf_pathAttachedCount) break;
  vec3 delta = mpf_pathAttachedPosition[i] - position;
  float squaredDistance = dot(delta, delta);
  if (squaredDistance >= mpf_pathAttachedRange[i] * mpf_pathAttachedRange[i]) continue;
  if (dot(mpf_pathAttachedColour[i], mpf_pathAttachedColour[i]) <= 0.0) continue;
  float distanceToLight = max(sqrt(squaredDistance), 0.001);
  vec3 toLight = delta / distanceToLight;
  float outward = max(dot(normalize(mpf_pathAttachedDirection[i]), -toLight), 0.0);
  if (outward <= 0.0) continue;
  float falloff = max(1.0 - distanceToLight / mpf_pathAttachedRange[i], 0.0);
  float energy = outward * falloff * falloff;
  float facing = max(dot(normal, toLight), 0.0);
  float highlight = pow(max(dot(normal, normalize(toLight + eye)), 0.0), 8.0);
  result += mpf_pathAttachedColour[i] * energy * (max(baseColour, vec3(0.035)) * facing * 3.0 + highlight * 0.30);
 }
 return result * mpf_pathLightOpacity;
}
bool mpf_path_light_safe(vec3 position){

 vec3 eyeDelta=mpf_pathProbe-position;
 float eyeSquared=dot(eyeDelta,eyeDelta);
 if(!mpf_path_finite(eyeDelta)||isnan(eyeSquared)||isinf(eyeSquared)||eyeSquared<=0.){
  return false;
 }
 vec3 eye=normalize(eyeDelta);
 for(int i=0;i<8;++i){
  if(i>=mpf_pathAttachedCount)break;
  vec3 delta=mpf_pathAttachedPosition[i]-position;
  float squaredDistance=dot(delta,delta),range=mpf_pathAttachedRange[i];
  if(!mpf_path_finite(delta)||!mpf_path_finite(mpf_pathAttachedDirection[i])
    ||!mpf_path_finite(mpf_pathAttachedColour[i])||isnan(squaredDistance)||isinf(squaredDistance)
    ||isnan(range)||isinf(range)||range<0.||isinf(range*range)){
   return false;
  }
  if(squaredDistance>=range*range)continue;
  float colourSquared=dot(mpf_pathAttachedColour[i],mpf_pathAttachedColour[i]);
  if(isnan(colourSquared)||isinf(colourSquared)){return false;}
  if(colourSquared<=0.)continue;
  float directionSquared=dot(mpf_pathAttachedDirection[i],mpf_pathAttachedDirection[i]);
  if(isnan(directionSquared)||isinf(directionSquared)||directionSquared<=0.){
   return false;
  }
  float distanceToLight=max(sqrt(squaredDistance),.001);
  vec3 toLight=delta/distanceToLight;
  float outward=max(dot(normalize(mpf_pathAttachedDirection[i]),-toLight),0.);
  if(outward<=0.)continue;

  vec3 halfway=toLight+eye;
  float halfwaySquared=dot(halfway,halfway);
  if(!mpf_path_finite(halfway)||isnan(halfwaySquared)||isinf(halfwaySquared)||halfwaySquared<=0.){
   return false;
  }

 }
 return true;
}

vec4 mpf_path_light_hit(MPFPathHit hit){
 vec4 colour=mpf_path_colour(hit);
 if(!complete)return vec4(0.);
 if(mpf_pathLightModels<0||mpf_pathLightModels>1||mpf_pathAttachedCount<0||mpf_pathAttachedCount>8){
  complete=false;return vec4(0.);
 }
 if(mpf_pathLightModels==0||mpf_pathAttachedCount==0)return vec4(0.);
 // A whole-cohort refusal precedes per-hit eligibility: a displaced historical
 // light primitive can overlap some OTHER winning base surface.
 if(mpf_pathLightCertified!=1||mpf_pathLightOrthographic!=0||mpf_pathLightTop<mpf_path_first||mpf_pathLightTop>mpf_path_completed
   ||isnan(mpf_pathLightOpacity)||isinf(mpf_pathLightOpacity)||mpf_pathLightOpacity<0.){
  complete=false;return vec4(0.);
 }
 if(!hit.lightFront||colour.a<=0.)return vec4(0.);
 int elements=textureSize(mpf_pathIndices);
 if(elements<0||elements%2!=0||mpf_path_sourceLineCount!=elements/2
   ||hit.line<0||hit.line>=mpf_path_sourceLineCount||mpf_pathTypeStride!=1){
  complete=false;return vec4(0.);
 }
 uint original=texelFetch(mpf_pathIndices,hit.line*2).r;
 if(mpf_pathVertexCount<0||original>=uint(mpf_pathVertexCount)){
  complete=false;return vec4(0.);
 }
 float kind=mpf_path_attr(int(original),mpf_pathType,mpf_pathTypeStride,0);
 if(!complete||kind<0.||kind>13.||kind!=floor(kind)){complete=false;return vec4(0.);}
 if((hit.line<mpf_pathLightTop&&kind!=1.)||(kind==11.&&mpf_path_showHelpers==0))return vec4(0.);
 float normalSquared=dot(hit.normal,hit.normal);
 if(!mpf_path_finite(hit.normal)||!mpf_path_finite(hit.centre)
   ||isnan(normalSquared)||isinf(normalSquared)||normalSquared<=0.){
  complete=false;return vec4(0.);
 }
 if(!mpf_path_light_safe(hit.centre)){complete=false;return vec4(0.);}
 vec3 raw=mpf_path_light_surface(hit.centre,hit.normal,colour.rgb);
 if(!mpf_path_finite(raw)){complete=false;return vec4(0.);}
 return complete?vec4(raw,colour.a):vec4(0.);
}
