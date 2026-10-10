// Distance-only original plate triangles. Include after the path query's
// finite/complete definitions. Unknown recipes or exhausted work refuse the
// whole correction; presentation alpha never removes native plate depth.
uniform samplerBuffer mpf_plateNodes;
uniform samplerBuffer mpf_plateVertices;
uniform samplerBuffer mpf_plateModels;
uniform usamplerBuffer mpf_plateTriangles;
uniform int mpf_plateNodeCount,mpf_plateVertexCount,mpf_plateTriangleCount,mpf_plateAssetCount;
int mpf_plateNodeWork,mpf_plateTriangleWork;
vec3 mpf_plate_point(uint vertex,uint asset){
 if(mpf_plateVertexCount<0||mpf_plateAssetCount<0||vertex>=uint(mpf_plateVertexCount)
    ||asset>=uint(mpf_plateAssetCount)||textureSize(mpf_plateVertices)/3<mpf_plateVertexCount
    ||textureSize(mpf_plateModels)/4<mpf_plateAssetCount){complete=false;return vec3(0.);}
 int index=int(vertex)*3,model=int(asset)*4;
 vec3 local=vec3(texelFetch(mpf_plateVertices,index).r,texelFetch(mpf_plateVertices,index+1).r,
                 texelFetch(mpf_plateVertices,index+2).r);
 // Stored original CPU matrix rows; mat4 consumes columns. Transpose changes
 // only element placement and preserves the native FLOAT32 model expression.
 mat4 matrix=transpose(mat4(texelFetch(mpf_plateModels,model),texelFetch(mpf_plateModels,model+1),
                      texelFetch(mpf_plateModels,model+2),texelFetch(mpf_plateModels,model+3)));
 vec4 world=matrix*vec4(local,1.);
 if(!mpf_path_finite(local)||!mpf_path_finite(world.xyz)||isnan(world.w)||isinf(world.w)||world.w!=1.){
  complete=false;return vec3(0.);
 }
 return world.xyz;
}
bool mpf_plate_triangle(vec3 o,vec3 d,vec3 a,vec3 b,vec3 c,float first,float last,out float distance){
 distance=0.;if(++mpf_plateTriangleWork>512){complete=false;return false;}
 vec3 e=b-a,f=c-a,p=cross(d,f);float det=dot(e,p);
 if(isnan(det)||isinf(det)){complete=false;return false;}if(det==0.)return false;
 float inverse=1./det;vec3 s=o-a;float u=dot(s,p)*inverse;
 if(isnan(u)||isinf(u)){complete=false;return false;}if(u<0.||u>1.)return false;
 vec3 q=cross(s,e);float v=dot(d,q)*inverse;
 if(isnan(v)||isinf(v)){complete=false;return false;}if(v<0.||u+v>1.)return false;
 float t=dot(f,q)*inverse;
 if(isnan(t)||isinf(t)){complete=false;return false;}
 if(t<=first||t>last)return false;distance=t;return true;
}
bool mpf_plate_box(vec3 o,vec3 d,vec3 low,vec3 high,float first,float last){
 for(int axis=0;axis<3;++axis){
  if(d[axis]==0.){if(o[axis]<low[axis]||o[axis]>high[axis])return false;}
  else{
   float a=(low[axis]-o[axis])/d[axis],b=(high[axis]-o[axis])/d[axis];
   if(isnan(a)||isinf(a)||isnan(b)||isinf(b)){complete=false;return false;}
   first=max(first,min(a,b));last=min(last,max(a,b));if(first>last)return false;
  }
 }
 return true;
}
float mpf_trace_plates(vec3 o,vec3 d,float first,float last,out bool found){
 found=false;mpf_plateNodeWork=mpf_plateTriangleWork=0;float best=last;
 float squared=dot(d,d);
 if(!complete||!mpf_path_finite(o)||!mpf_path_finite(d)||isnan(squared)||isinf(squared)||squared<=0.
   ||isnan(first)||isinf(first)||isnan(last)||isinf(last)||first<0.||last<=first
   ||mpf_plateNodeCount<0||mpf_plateNodeCount>16777215||mpf_plateNodeCount>textureSize(mpf_plateNodes)/2
   ||mpf_plateVertexCount<0||mpf_plateVertexCount>textureSize(mpf_plateVertices)/3
   ||mpf_plateTriangleCount<0||mpf_plateTriangleCount>textureSize(mpf_plateTriangles)
   ||mpf_plateAssetCount<0||mpf_plateAssetCount>textureSize(mpf_plateModels)/4){complete=false;return 0.;}
 if(mpf_plateTriangleCount==0){if(mpf_plateNodeCount!=0)complete=false;return 0.;}
 if(mpf_plateNodeCount==0||mpf_plateAssetCount==0){complete=false;return 0.;}
 int stack[64];int size=1;stack[0]=0;
 while(size>0&&complete){
  if(++mpf_plateNodeWork>2048){complete=false;break;}
  int node=stack[--size];if(node<0||node>=mpf_plateNodeCount){complete=false;break;}
  vec4 low=texelFetch(mpf_plateNodes,node*2),high=texelFetch(mpf_plateNodes,node*2+1);
  if(!mpf_path_finite(low.xyz)||!mpf_path_finite(high.xyz)||isnan(low.w)||isinf(low.w)
    ||isnan(high.w)||isinf(high.w)||any(greaterThan(low.xyz,high.xyz))
    ||low.w!=floor(low.w)||high.w!=floor(high.w)){complete=false;break;}
  if(!mpf_plate_box(o,d,low.xyz,high.xyz,first,best))continue;
  if(high.w<0.){
   if(low.w<0.||low.w>float(mpf_plateTriangleCount)||high.w<-8.
      ||-high.w>float(mpf_plateTriangleCount)-low.w){complete=false;break;}
   int start=int(low.w),count=int(-high.w);
   for(int i=0;i<count&&complete;++i){
    uvec4 record=texelFetch(mpf_plateTriangles,start+i);
    vec3 a=mpf_plate_point(record.x,record.w),b=mpf_plate_point(record.y,record.w),c=mpf_plate_point(record.z,record.w);
    if(!complete)break;
    float distance;if(mpf_plate_triangle(o,d,a,b,c,first,best,distance)){
     found=true;best=distance;
    }
   }
  }else{
   if(low.w<0.||low.w>=float(mpf_plateNodeCount)||high.w>=float(mpf_plateNodeCount)||size>62){complete=false;break;}
   stack[size++]=int(low.w);stack[size++]=int(high.w);
  }
 }
 if(!complete){found=false;return 0.;}
 return found?best:0.;
}
