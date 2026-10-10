uniform int mpf_recoveryEnabled,mpf_recoveryPlates;
uniform vec3 mpf_recoveryPlateLow,mpf_recoveryPlateHigh;
bool mpf_recovery_separated(vec3 origin){
 if(mpf_recoveryPlates==0)return true;
 if(mpf_recoveryPlates!=1||!mpf_path_finite(mpf_recoveryPlateLow)
   ||!mpf_path_finite(mpf_recoveryPlateHigh)||!mpf_path_finite(mpf_pathHitLow)
   ||!mpf_path_finite(mpf_pathHitHigh)||any(greaterThan(mpf_recoveryPlateLow,mpf_recoveryPlateHigh))
   ||any(greaterThan(mpf_pathHitLow,mpf_pathHitHigh)))return false;
 // The whole origin-to-hit hull lies outside ALL original plate geometry.
 // Strict separation needs no guessed triangle-distance epsilon. Equality
 // and bed/heightmesh overlap retain the original map branch.
 return any(lessThan(max(origin,mpf_pathHitHigh),mpf_recoveryPlateLow))
     ||any(greaterThan(min(origin,mpf_pathHitLow),mpf_recoveryPlateHigh));
}
vec3 environmentReflection(vec3 direction,float roughness,out float confidence){
 if(mpf_recoveryEnabled==1&&mpf_path_finite(direction)&&mpf_path_finite(v_position)
   &&dot(direction,direction)>.5){
  vec3 tangent=normalize(cross(direction,abs(direction.y)<.9?vec3(0.,1.,0.):vec3(1.,0.,0.)));
  vec3 bitangent=cross(direction,tangent);float cone=roughness*roughness*.7;
  vec3 colour=vec3(0.);bool admitted=true;
  for(int i=0;i<7;++i){
   vec3 offset=vec3(0.);
   if(i==1)offset=tangent*cone;if(i==2)offset=-tangent*cone;
   if(i==3)offset=bitangent*cone;if(i==4)offset=-bitangent*cone;
   if(i==5)offset=(tangent+bitangent)*cone*.7;if(i==6)offset=-(tangent+bitangent)*cone*.7;
   vec3 ray=normalize(direction+offset);
   MPFPathHit hit=mpf_trace_paths(v_position,ray,.01,u_probeFar*4.);
   if(!complete||hit.line<0||!mpf_recovery_separated(v_position)){admitted=false;break;}
   vec4 sampleColour=mpf_path_resolved_hit(hit);
   if(!complete||!mpf_path_finite(sampleColour.rgb)){admitted=false;break;}
   colour+=sampleColour.rgb*(i==0?.4:.1);
  }
  if(admitted){confidence=1.;return colour;}
 }
 return mapEnvironmentReflection(direction,roughness,confidence);
}
