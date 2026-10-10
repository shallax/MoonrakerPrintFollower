// Owned GLSL410 native path query. CPU source/tree certificates and retained
// original VBO/EBO leases are required; this include does not own GL objects.
// Caller supplies post-native-VS MPFPathLine via immutable borrowed VBO/EBO.
// Keep returned normal interpolation and centreline for native hit shading.
struct MPFPathLine {vec3 a;float width;vec3 b;float height;float kind;float previous;float visibility;};
struct MPFPathHit {float distance;int line;vec3 normal;vec3 centre;bool start;bool lightFront;};
MPFPathLine mpf_source_line(int line,bool history);
uniform samplerBuffer mpf_path_nodes;
uniform usamplerBuffer mpf_path_aliasLines;
uniform int mpf_path_nodeCount,mpf_path_sourceLineCount;
uniform int mpf_path_first,mpf_path_historyEnd,mpf_path_completed,mpf_path_aliasCount;
uniform int mpf_path_showTravel,mpf_path_showHelpers,mpf_path_showSkin,mpf_path_showInfill,mpf_path_showStarts;
uniform vec3 mpf_pathProbe;
// Optional co-depth line collection for native additive LEQUAL composition.
// Ordinary nearest-hit queries keep their original admission and cost. The
// caller must separately qualify intra-line raster edge ownership/storage.
uniform int mpf_pathCollectLight;
MPFPathHit mpf_pathLightHits[8];int mpf_pathLightHitCount;
int nodeWork,facetWork;bool complete;
float mpf_path_nearDistance,mpf_path_farDistance,mpf_path_baryU,mpf_path_baryV;
// Explicit hit validity: no finite miss sentinel.
vec3 mpf_path_candidateNormal,mpf_path_candidateCentre;bool mpf_path_candidateStart,mpf_path_candidateValid,mpf_path_candidateFront;
vec3 mpf_path_candidateLow,mpf_path_candidateHigh,mpf_pathHitLow,mpf_pathHitHigh;
bool mpf_path_finite(vec3 value){return !any(isnan(value))&&!any(isinf(value));}
const int mpf_path_endpoint[26]=int[26](0,1,0,1,0,1,0,1,0,1,0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1);
const int mpf_path_side[26]=int[26](0,0,1,1,2,2,3,3,0,0,0,1,4,2,2,3,4,0,2,1,5,0,0,3,5,2);
const int mpf_path_pattern[48]=int[48](0,1,2,2,1,3,2,3,4,4,3,5,4,5,6,6,5,7,6,7,8,8,7,9,10,11,12,12,11,13,14,15,16,16,15,17,18,19,20,20,19,21,22,23,24,24,23,25);
bool mpf_path_tri(vec3 o,vec3 d,vec3 a,vec3 b,vec3 c,out float distance){
 distance=0.0;if(++facetWork>512){complete=false;return false;}vec3 e=b-a,f=c-a,p=cross(d,f);float det=dot(e,p);
 if(isnan(det)||isinf(det)){complete=false;return false;}if(det==0.0)return false;
 float inv=1.0/det;vec3 s=o-a;float u=dot(s,p)*inv;
 if(isnan(u)||isinf(u)){complete=false;return false;}if(u<0.0||u>1.0)return false;
 vec3 q=cross(s,e);float v=dot(d,q)*inv;
 if(isnan(v)||isinf(v)){complete=false;return false;}if(v<0.0||u+v>1.0)return false;
 float t=dot(f,q)*inv;
 if(isnan(t)||isinf(t)){complete=false;return false;}if(t<=mpf_path_nearDistance||t>=mpf_path_farDistance)return false;
 mpf_path_baryU=u;mpf_path_baryV=v;distance=t;return true;
}
bool mpf_path_box(vec3 o,vec3 d,vec3 low,vec3 high,float far){
 if(!mpf_path_finite(low)||!mpf_path_finite(high)){complete=false;return false;}
 float near=mpf_path_nearDistance;
 for(int axis=0;axis<3;++axis){
  if(d[axis]==0.){if(o[axis]<low[axis]||o[axis]>high[axis])return false;}
  else{
   float a=(low[axis]-o[axis])/d[axis],b=(high[axis]-o[axis])/d[axis];
   if(!mpf_path_finite(vec3(a,b,0.))){complete=false;return false;}
   near=max(near,min(a,b));far=min(far,max(a,b));if(near>far)return false;
  }
 }
 return true;
}
void mpf_path_facet(vec3 o,vec3 d,vec3 a,vec3 b,vec3 c,vec3 na,vec3 nb,vec3 nc,vec3 ca,vec3 cb,vec3 cc,bool isStart,bool reversed,inout float best){
 float t;if(!mpf_path_tri(o,d,a,b,c,t)||t>=best)return;
 vec3 normal=na*(1.0-mpf_path_baryU-mpf_path_baryV)+nb*mpf_path_baryU+nc*mpf_path_baryV;
 vec3 centre=ca*(1.0-mpf_path_baryU-mpf_path_baryV)+cb*mpf_path_baryU+cc*mpf_path_baryV;
 if(!mpf_path_finite(normal)||!mpf_path_finite(centre)){complete=false;return;}
 // Attached capture culls from its frozen probe, NOT from the reflected ray.
 float facing=dot(cross(b-a,c-a),mpf_pathProbe-a);
 if(isnan(facing)||isinf(facing)){complete=false;return;}
 best=t;mpf_path_candidateStart=isStart;mpf_path_candidateNormal=normal;mpf_path_candidateCentre=centre;mpf_path_candidateValid=true;
 // GL_CCW uses original strip winding, independently of smooth normals.
 mpf_path_candidateFront=reversed?facing<0.:facing>0.;
}

const vec3 mpf_path_startSign[14]=vec3[14](vec3(1,1,1),vec3(-1,1,1),vec3(1,-1,1),vec3(-1,-1,1),
 vec3(-1,-1,-1),vec3(-1,1,1),vec3(-1,1,-1),vec3(1,1,1),vec3(1,1,-1),vec3(1,-1,1),
 vec3(1,-1,-1),vec3(-1,-1,-1),vec3(1,1,-1),vec3(-1,1,-1));
vec3 mpf_path_offset(int side,vec3 r,vec3 up,vec3 h){
 if(side==0)return -r;if(side==1)return up;if(side==2)return r;
 if(side==3)return -up;if(side==4)return -h;return h;
}
vec3 mpf_path_travelCorner(int corner,vec3 a,vec3 b,vec3 h,vec3 r,vec3 up){
 if(corner==0||corner==10)return a-h+up;
 if(corner==1||corner==9)return a-r+up;
 if(corner==2||corner==8||corner==11)return a+r+up;
 if(corner==3||corner==7)return b-r+up;
 if(corner==4||corner==6)return b+r+up;return b+h+up;
}
bool mpf_path_lineHit(int index,vec3 o,vec3 d,float limit,out float distance){
 distance=mpf_path_farDistance;mpf_path_candidateValid=false;mpf_path_candidateStart=false;
 mpf_path_candidateNormal=vec3(0.);mpf_path_candidateCentre=vec3(0.);mpf_path_candidateFront=false;
 if(index<0||index>=mpf_path_sourceLineCount){complete=false;return false;}
 if(index<mpf_path_first||index>=mpf_path_completed)return false;
 bool history=index<mpf_path_historyEnd;MPFPathLine line=mpf_source_line(index,history);
 if(!complete)return false;
 if(!mpf_path_finite(line.a)||!mpf_path_finite(line.b)
   ||!mpf_path_finite(vec3(line.width,line.height,line.visibility))
   ||!mpf_path_finite(vec3(line.kind,line.previous,0.))||line.width<0.||line.height<0.
   ||line.kind<0.||line.kind>13.||line.kind!=floor(line.kind)
   ||line.previous<0.||line.previous>13.||line.previous!=floor(line.previous)){
  complete=false;return false;
 }
 int kind=int(line.kind),prev=int(line.previous);bool travel=kind==8||kind==9||kind==12||kind==13;
 if((line.visibility==0.&&!travel)||(travel&&mpf_path_showTravel==0)
   ||((kind==4||kind==5||kind==7||kind==10||(!history&&kind==11))&&mpf_path_showHelpers==0)
   ||((kind==1||kind==2||kind==3)&&mpf_path_showSkin==0)||(kind==6&&mpf_path_showInfill==0))return false;
 vec3 a=line.a,b=line.b,delta=b-a;if(all(equal(delta,vec3(0.))))return false;
 vec3 radial=delta.y==0.?vec3(delta.z,0.,-delta.x):
   delta.x==0.&&delta.z==0.?vec3(1.,0.,-1.):cross(delta,vec3(delta.x,0.,delta.z));
 float ds=dot(delta,delta),rs=dot(radial,radial);
 if(!mpf_path_finite(vec3(ds,rs,0.))||ds<=1e-12||rs<=1e-12){complete=false;return false;}
 float sx=travel?.05:line.width/2.+.01,sy=line.height/2.+.01;
 vec3 axialNormal=normalize(delta),radialNormal=normalize(radial),upNormal=vec3(0.,1.,0.);
 vec3 h=axialNormal*sx,r=radialNormal*sx,up=upNormal*sy;
 if(!mpf_path_finite(h)||!mpf_path_finite(r)||!mpf_path_finite(up)){complete=false;return false;}
 // Conservative original-segment interval, including travel's combined
 // axial/radial+up corners and start minima. This only rejects misses; it
 // never substitutes geometry. Float arithmetic is padded outwards.
 float radius=2.*max(max(sx,sy),.05)*1.00001;
 vec3 extent=travel?max(abs(h),abs(r))+abs(up):max(max(abs(h),abs(r)),abs(up));
 if(mpf_path_showStarts==1&&!history&&((kind==1&&prev!=1)||(kind==4&&prev!=4)))
  extent=max(extent,vec3(max(.05,sx),max(.05,sy),max(.05,sx)));
 vec3 margin=max(max(abs(a),abs(b))*0.000002,vec3(.00001))+vec3(radius*.000002);
 mpf_path_candidateLow=min(a,b)-extent-margin;
 mpf_path_candidateHigh=max(a,b)+extent+margin;
 if(!mpf_path_box(o,d,mpf_path_candidateLow,mpf_path_candidateHigh,limit))return false;
 // Surviving rays retain the exact original facet order/payload expressions.
 float best=mpf_path_farDistance;
 if(travel){
  for(int k=2;k<12&&complete;++k){
   int i=k-2,j=k-1;
   mpf_path_facet(o,d,mpf_path_travelCorner(i,a,b,h,r,up),mpf_path_travelCorner(j,a,b,h,r,up),
    mpf_path_travelCorner(k,a,b,h,r,up),vec3(0.,1.,0.),vec3(0.,1.,0.),vec3(0.,1.,0.),
    i<3||i>=8?a:b,j<3||j>=8?a:b,k<3||k>=8?a:b,false,k%2!=0,best);
  }
 }else{
  for(int k=0;k<48&&complete;k+=3){
   int i=mpf_path_pattern[k],j=mpf_path_pattern[k+1],q=mpf_path_pattern[k+2];
   vec3 oi=mpf_path_offset(mpf_path_side[i],r,up,h),oj=mpf_path_offset(mpf_path_side[j],r,up,h),
        oq=mpf_path_offset(mpf_path_side[q],r,up,h);
   vec3 ci=mpf_path_endpoint[i]==0?a:b,cj=mpf_path_endpoint[j]==0?a:b,cq=mpf_path_endpoint[q]==0?a:b;
   mpf_path_facet(o,d,ci+oi,cj+oj,cq+oq,
    mpf_path_offset(mpf_path_side[i],radialNormal,upNormal,axialNormal),
    mpf_path_offset(mpf_path_side[j],radialNormal,upNormal,axialNormal),
    mpf_path_offset(mpf_path_side[q],radialNormal,upNormal,axialNormal),ci,cj,cq,false,false,best);
  }
 }
 if(mpf_path_showStarts==1&&!history&&((kind==1&&prev!=1)||(kind==4&&prev!=4))){
  vec3 scale=vec3(max(.05,sx),max(.05,sy),max(.05,sx));
  for(int k=2;k<14&&complete;++k){
   vec3 si=mpf_path_startSign[k-2],sj=mpf_path_startSign[k-1],sq=mpf_path_startSign[k];
   vec3 vi=a+si*scale,vj=a+sj*scale,vq=a+sq*scale;
   mpf_path_facet(o,d,vi,vj,vq,normalize(si),normalize(sj),normalize(sq),vi,vj,vq,true,k%2!=0,best);
  }
 }
 distance=best;return complete&&mpf_path_candidateValid;
}
bool mpf_path_node(int index,out vec4 lo,out vec4 hi){
 lo=hi=vec4(0.);
 if(index<0||index>=mpf_path_nodeCount){complete=false;return false;}
 lo=texelFetch(mpf_path_nodes,index*2);hi=texelFetch(mpf_path_nodes,index*2+1);
 if(any(isnan(lo))||any(isinf(lo))||any(isnan(hi))||any(isinf(hi))||any(greaterThan(lo.xyz,hi.xyz))
   ||lo.w!=floor(lo.w)||hi.w!=floor(hi.w)) {complete=false;return false;}
 if(hi.w<0.){
  if(hi.w < -32.||lo.w<0.||lo.w>float(mpf_path_sourceLineCount)
    ||-hi.w>float(mpf_path_sourceLineCount)-lo.w){complete=false;return false;}
 }else if(lo.w<0.||lo.w>=float(mpf_path_nodeCount)||hi.w>=float(mpf_path_nodeCount)){
  complete=false;return false;
 }
 return true;
}
bool mpf_path_nearNode(int index,vec3 origin,vec3 direction,float limit,out float near,out vec4 lo,out vec4 hi){
 near=mpf_path_nearDistance;if(!mpf_path_node(index,lo,hi))return false;
 float far=limit;
 for(int axis=0;axis<3;++axis){
  if(direction[axis]==0.){if(origin[axis]<lo[axis]||origin[axis]>hi[axis])return false;}
  else{
   float a=(lo[axis]-origin[axis])/direction[axis],b=(hi[axis]-origin[axis])/direction[axis];
   if(!mpf_path_finite(vec3(a,b,0.))){complete=false;return false;}
   near=max(near,min(a,b));far=min(far,max(a,b));if(near>far)return false;
  }
 }
 return near<=limit; // Equal-distance original-ID ties must remain admitted.
}
void mpf_path_record_hit(float t,int line,inout MPFPathHit hit){
 if(t>hit.distance)return;
 MPFPathHit candidate=MPFPathHit(t,line,mpf_path_candidateNormal,mpf_path_candidateCentre,
  mpf_path_candidateStart,mpf_path_candidateFront);
 if(mpf_pathCollectLight==1){
  if(t<hit.distance)mpf_pathLightHitCount=0;
  bool recorded=false;
  for(int i=0;i<mpf_pathLightHitCount;++i){
   if(mpf_pathLightHits[i].line==line)recorded=true; // Alias replay is not another draw.
  }
  if(!recorded){
   if(mpf_pathLightHitCount>=8){complete=false;return;}
   int slot=mpf_pathLightHitCount;
   // Native additive draw order is original EBO order, not BVH visitation.
   for(int i=0;i<mpf_pathLightHitCount;++i){
    if(line<mpf_pathLightHits[i].line){slot=i;break;}
   }
   for(int i=mpf_pathLightHitCount;i>slot;--i)mpf_pathLightHits[i]=mpf_pathLightHits[i-1];
   mpf_pathLightHits[slot]=candidate;++mpf_pathLightHitCount;
  }
 }
 if(t<hit.distance||(t==hit.distance&&(hit.line<0||line<hit.line))){
  hit=candidate;mpf_pathHitLow=mpf_path_candidateLow;mpf_pathHitHigh=mpf_path_candidateHigh;
 }
}
MPFPathHit mpf_trace_paths(vec3 origin,vec3 direction,float firstDistance,float lastDistance){
 nodeWork=facetWork=0;mpf_pathLightHitCount=0;complete=true;
 mpf_pathHitLow=mpf_pathHitHigh=vec3(0.);
 MPFPathHit hit=MPFPathHit(0.,-1,vec3(0.),vec3(0.),false,false);
 if(!mpf_path_finite(origin)||!mpf_path_finite(direction)||!mpf_path_finite(mpf_pathProbe)
   ||!mpf_path_finite(vec3(firstDistance,lastDistance,dot(direction,direction)))
   ||dot(direction,direction)<=0.||firstDistance<0.||lastDistance<=firstDistance
   ||mpf_path_nodeCount<0||mpf_path_nodeCount>16777215
   ||mpf_path_nodeCount>textureSize(mpf_path_nodes)/2
   ||mpf_path_sourceLineCount<0||mpf_path_sourceLineCount>16777215
   ||(mpf_path_nodeCount==0)!=(mpf_path_sourceLineCount==0)
   ||mpf_path_first<0||mpf_path_completed<mpf_path_first||mpf_path_completed>mpf_path_sourceLineCount
   ||mpf_path_historyEnd<0||mpf_path_historyEnd>mpf_path_completed
   ||mpf_path_aliasCount<0||mpf_path_aliasCount>32||mpf_path_aliasCount>textureSize(mpf_path_aliasLines)
   ||mpf_pathCollectLight<0||mpf_pathCollectLight>1){
  complete=false;hit.line=-2;return hit;
 }
 hit.distance=lastDistance;mpf_path_nearDistance=firstDistance;mpf_path_farDistance=lastDistance;
 int stack[64],top=0;if(mpf_path_nodeCount>0)stack[top++]=0;
 while(top>0&&complete){
  if(++nodeWork>2048){complete=false;break;}
  int index=stack[--top];float entry;vec4 lo,hi;
  if(!mpf_path_nearNode(index,origin,direction,hit.distance,entry,lo,hi))continue;
  if(hi.w<0.){
   int end=int(lo.w)-int(hi.w);
   for(int line=int(lo.w);line<end&&complete;++line){
    float t;if(mpf_path_lineHit(line,origin,direction,hit.distance,t))mpf_path_record_hit(t,line,hit);
   }
  }else{
   int left=int(lo.w),right=int(hi.w);float ln,rn;
   vec4 childLow,childHigh;
   bool lv=mpf_path_nearNode(left,origin,direction,hit.distance,ln,childLow,childHigh),
        rv=mpf_path_nearNode(right,origin,direction,hit.distance,rn,childLow,childHigh);
   if(top+(lv?1:0)+(rv?1:0)>64){complete=false;break;}
   if(lv&&rv){if(ln<rn){stack[top++]=right;stack[top++]=left;}else{stack[top++]=left;stack[top++]=right;}}
   else if(lv)stack[top++]=left;else if(rv)stack[top++]=right;
  }
 }
 for(int i=0;i<mpf_path_aliasCount&&complete;++i){
  uint id=texelFetch(mpf_path_aliasLines,i).r;
  if(id>=uint(mpf_path_sourceLineCount)){complete=false;break;}
  int line=int(id);float t;if(mpf_path_lineHit(line,origin,direction,hit.distance,t))mpf_path_record_hit(t,line,hit);
 }
 if(!complete){mpf_pathLightHitCount=0;hit=MPFPathHit(lastDistance,-2,vec3(0.),vec3(0.),false,false);}
 return hit;
}
