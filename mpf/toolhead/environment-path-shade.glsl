// Native layers3d / layers3d_shadow hit-local appearance. Include after the
// original path query and source decoder. This is not a runtime shader hook.
// No cubemap RGB is reused for a geometry hit hidden from the capture probe.
uniform int mpf_pathView,mpf_pathColourOffset,mpf_pathMaterialColourOffset,mpf_pathFeedrateOffset;
uniform int mpf_pathColourStride,mpf_pathMaterialColourStride,mpf_pathFeedrateStride;
// Metric lanes: feedrate, thickness, line width, flow rate.
uniform vec4 mpf_pathMinimum,mpf_pathMaximum,mpf_pathStartsColour;
uniform vec3 mpf_pathCameraLight,mpf_pathMinimumAlbedo;

bool mpf_path_finite4(vec4 value){return !any(isnan(value))&&!any(isinf(value));}
vec4 mpf_path_colour_attribute(int vertex,int offset,int stride){
 if(stride!=4){complete=false;return vec4(0.);}
 return vec4(mpf_path_attr(vertex,offset,stride,0),mpf_path_attr(vertex,offset,stride,1),
  mpf_path_attr(vertex,offset,stride,2),mpf_path_attr(vertex,offset,stride,3));
}
float mpf_path_gradient(float value,float minimum,float maximum){
 if(isnan(minimum)||isinf(minimum)||isnan(maximum)||isinf(maximum)){
  complete=false;return 0.;
 }
 float result=abs(maximum-minimum)<.0001?.5:(value-minimum)/(maximum-minimum);
 if(isnan(result)||isinf(result)){complete=false;return 0.;}return result;
}
vec4 mpf_path_colour(MPFPathHit hit){
 // Validate original EBO addressing BEFORE multiplying or narrowing uint.
 int elements=textureSize(mpf_pathIndices);
 if(!complete||mpf_path_first<0||mpf_path_completed<mpf_path_first
   ||mpf_path_completed>mpf_path_sourceLineCount||mpf_path_historyEnd<0||mpf_path_historyEnd>mpf_path_completed
   ||hit.line<mpf_path_first||hit.line>=mpf_path_completed
   ||hit.line<0||elements<0||elements%2!=0||mpf_path_sourceLineCount!=elements/2
   ||hit.line>=mpf_path_sourceLineCount){complete=false;return vec4(0.);}
 if(hit.line<mpf_path_historyEnd)return vec4(.4,.4,.4,.9);
 if(hit.start){
  if(!mpf_path_finite4(mpf_pathStartsColour)){complete=false;return vec4(0.);}
  return mpf_pathStartsColour;
 }
 uint original=texelFetch(mpf_pathIndices,hit.line*2+1).r;
 if(mpf_pathVertexCount<0||original>=uint(mpf_pathVertexCount)){
  complete=false;return vec4(0.);
 }
 int vertex=int(original);
 if(mpf_pathView==0)return mpf_path_colour_attribute(vertex,mpf_pathMaterialColourOffset,mpf_pathMaterialColourStride);
 if(mpf_pathView==1)return mpf_path_colour_attribute(vertex,mpf_pathColourOffset,mpf_pathColourStride);
 if(mpf_pathView<2||mpf_pathView>5||mpf_pathDimensionsStride!=2){complete=false;return vec4(0.);}
 float width=mpf_path_attr(vertex,mpf_pathDimensions,mpf_pathDimensionsStride,0),
       height=mpf_path_attr(vertex,mpf_pathDimensions,mpf_pathDimensionsStride,1);
 if(mpf_pathView==2||mpf_pathView==4){
  if(mpf_pathView==2&&mpf_pathFeedrateStride!=1){complete=false;return vec4(0.);}
  float value=mpf_pathView==2
   ?mpf_path_gradient(mpf_path_attr(vertex,mpf_pathFeedrateOffset,mpf_pathFeedrateStride,0),mpf_pathMinimum.x,mpf_pathMaximum.x)
   :mpf_path_gradient(width,mpf_pathMinimum.z,mpf_pathMaximum.z);
  float green=1.-abs(1.-4.*value);if(value>.375)green=.5;
  return vec4(value,green,max(1.-4.*value,0.),1.);
 }
 if(mpf_pathView==3){
  float value=mpf_path_gradient(height,mpf_pathMinimum.y,mpf_pathMaximum.y);
  float red=min(max(4.*value-2.,0.),1.),green=min(1.5*value,.75);
  if(value>.75)green=value;
  return vec4(red,green,.75-abs(.25-value),1.);
 }
 if(mpf_pathFeedrateStride!=1||!mpf_path_finite4(mpf_pathMinimum)||!mpf_path_finite4(mpf_pathMaximum)){
  complete=false;return vec4(0.);
 }
 float flow=width*height*mpf_path_attr(vertex,mpf_pathFeedrateOffset,mpf_pathFeedrateStride,0);
 float t=abs(mpf_pathMinimum.w-mpf_pathMaximum.w)<.0001?0.
  :2.*((flow-mpf_pathMinimum.w)/(mpf_pathMaximum.w-mpf_pathMinimum.w))-1.;
 if(isnan(flow)||isinf(flow)||isnan(t)||isinf(t)){complete=false;return vec4(0.);}
 return vec4(clamp(1.5-abs(2.*t-1.),0.,1.),clamp(1.5-abs(2.*t),0.,1.),clamp(1.5-abs(2.*t+1.),0.,1.),1.);
}
vec4 mpf_path_shade_hit(MPFPathHit hit){
 lowp vec4 colour=mpf_path_colour(hit);
 vec3 delta=mpf_pathCameraLight-hit.centre;
 float normalSquared=dot(hit.normal,hit.normal),lightSquared=dot(delta,delta);
 if(!complete||!mpf_path_finite4(colour)||!mpf_path_finite(hit.normal)||!mpf_path_finite(hit.centre)
   ||!mpf_path_finite(mpf_pathMinimumAlbedo)||!mpf_path_finite(delta)
   ||!mpf_path_finite(vec3(normalSquared,lightSquared,0.))||normalSquared<=0.||lightSquared<=0.){
  complete=false;return vec4(0.);
 }
 mediump vec4 finalColor=vec4(0.);
 float alpha=colour.a;
 if(hit.line<mpf_path_historyEnd)finalColor.rgb+=colour.rgb*.3;
 else finalColor.rgb+=colour.rgb*.2+mpf_pathMinimumAlbedo;
 highp vec3 normal=normalize(hit.normal);
 highp vec3 light_dir=normalize(delta);
 highp float NdotL=clamp(dot(normal,light_dir),0.,1.);
 finalColor+=(NdotL*colour);
 finalColor.a=alpha;
 if(!mpf_path_finite4(finalColor)){complete=false;return vec4(0.);}
 // Raw native FS result: the owner must preserve base RGBA8 storage BEFORE
 // any alpha-scaled attached-light blend. No attached-light parity is claimed
 // here; that separate pass also needs original winding/top/alias admission.
 return finalColor;
}
