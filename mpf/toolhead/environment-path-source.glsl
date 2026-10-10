// Original planar FLOAT32 VBO / full UINT32 EBO decoder. Publication admission
// certifies every field and generation; the graphics owner retains ready reads.
// This resource owns no GL object and is not a runtime shader hook.
uniform samplerBuffer mpf_pathVertices;
uniform usamplerBuffer mpf_pathIndices;
uniform int mpf_pathVertexCount,mpf_pathScalarCount;
uniform int mpf_pathPosition,mpf_pathDimensions,mpf_pathType;
uniform int mpf_pathPositionStride,mpf_pathDimensionsStride,mpf_pathTypeStride;
uniform int mpf_pathPrevious,mpf_pathExtruder;
uniform int mpf_pathPreviousStride,mpf_pathExtruderStride;
uniform mat4 mpf_pathModel,mpf_pathVisibility;
uniform int mpf_pathClipEnabled;
uniform vec3 mpf_pathLast,mpf_pathNext;
uniform float mpf_pathRatio;

float mpf_path_attr(int vertex,int offset,int stride,int component){
 // Division checks precede multiplication, addition and texel fetch. Counts
 // must describe the WHOLE uploaded VBO, not a staged or truncated buffer.
 if(mpf_pathVertexCount<0||vertex<0||vertex>=mpf_pathVertexCount
   ||mpf_pathScalarCount!=textureSize(mpf_pathVertices)||offset<0
   ||stride<=0||component<0||component>=stride||offset>=mpf_pathScalarCount
   ||component>=mpf_pathScalarCount-offset
   ||vertex>(mpf_pathScalarCount-offset-1-component)/stride){
  complete=false;return 0.;
 }
 float result=texelFetch(mpf_pathVertices,offset+vertex*stride+component).r;
 if(isnan(result)||isinf(result)){complete=false;return 0.;}return result;
}
vec3 mpf_path_point(int vertex,bool history){
 vec3 point=vec3(mpf_path_attr(vertex,mpf_pathPosition,mpf_pathPositionStride,0),
  mpf_path_attr(vertex,mpf_pathPosition,mpf_pathPositionStride,1),
  mpf_path_attr(vertex,mpf_pathPosition,mpf_pathPositionStride,2));
 if(!history&&mpf_pathClipEnabled==1){
  if(!mpf_path_finite(mpf_pathLast)||!mpf_path_finite(mpf_pathNext)
    ||isnan(mpf_pathRatio)||isinf(mpf_pathRatio)||mpf_pathRatio<0.||mpf_pathRatio>1.){
   complete=false;return vec3(0.);
  }
  if(all(equal(point,mpf_pathNext)))point=mix(mpf_pathLast,mpf_pathNext,mpf_pathRatio);
 }
 // Original VS ordering: clip raw local position, then endpoint half-height,
 // then the final FLOAT32 model. Geometry offsets remain world-space values.
 point.y-=mpf_path_attr(vertex,mpf_pathDimensions,mpf_pathDimensionsStride,1)*.5;
 vec4 world=mpf_pathModel*vec4(point,1.);
 if(!mpf_path_finite(world.xyz)||isnan(world.w)||isinf(world.w)||world.w!=1.){
  complete=false;return vec3(0.);
 }
 return world.xyz;
}
MPFPathLine mpf_source_line(int line,bool history){
 MPFPathLine result=MPFPathLine(vec3(0.),0.,vec3(0.),0.,0.,0.,0.);
 int elements=textureSize(mpf_pathIndices);
 if(line<0||line>=mpf_path_sourceLineCount||elements<0||elements%2!=0
   ||mpf_path_sourceLineCount!=elements/2||mpf_pathClipEnabled<0||mpf_pathClipEnabled>1
   ||mpf_pathPositionStride!=3||mpf_pathDimensionsStride!=2
   ||mpf_pathTypeStride!=1||mpf_pathPreviousStride!=1||mpf_pathExtruderStride!=1){
  complete=false;return result;
 }
 // The count comparison bounds line*2; check both unsigned original vertex
 // indices before narrowing. A uint above INT_MAX must never become negative.
 uint ai=texelFetch(mpf_pathIndices,line*2).r,bi=texelFetch(mpf_pathIndices,line*2+1).r;
 if(mpf_pathVertexCount<0||ai>=uint(mpf_pathVertexCount)||bi>=uint(mpf_pathVertexCount)){
  complete=false;return result;
 }
 int a=int(ai),b=int(bi);
 float extruder=mpf_path_attr(a,mpf_pathExtruder,mpf_pathExtruderStride,0);
 if(extruder<0.||extruder>15.||extruder!=floor(extruder)){
  complete=false;return result;
 }
 int slot=int(extruder);
 result.a=mpf_path_point(a,history);result.b=mpf_path_point(b,history);
 result.width=mpf_path_attr(b,mpf_pathDimensions,mpf_pathDimensionsStride,0);
 result.height=mpf_path_attr(b,mpf_pathDimensions,mpf_pathDimensionsStride,1);
 result.kind=mpf_path_attr(a,mpf_pathType,mpf_pathTypeStride,0);
 result.previous=mpf_path_attr(a,mpf_pathPrevious,mpf_pathPreviousStride,0);
 result.visibility=mpf_pathVisibility[slot%4][slot/4];
 return result;
}
