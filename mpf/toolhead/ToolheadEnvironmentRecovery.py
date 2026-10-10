"""Frozen path gap recovery, owned by the completed environment-map pair.

Only separated plate/path ordering is admitted. Uncertain geometry, unsupported
recipes and budget refusals keep the normal map; failed graphics restoration
retains the complete cohort instead of releasing a potentially live VBO.
"""
import hashlib
import re
import ctypes
from contextlib import contextmanager
from weakref import ref

import numpy as np

from .ToolheadCaptureValues import thaw_uniform
from .ToolheadEnvironmentGeometry import PathGeometry, GeometryUncertain
from .ToolheadEnvironmentPaths import admit_path_inputs, freeze_path_prefix
from .ToolheadEnvironmentPlates import prepare_plate_source
from .ToolheadEnvironmentRadiance import freeze_path_radiance, _plain
from ..resources.PluginPaths import plugin_path
from .ToolheadGLState import preserved_state, procedure

BUDGET = 256 * 1024**2
CAPTURE_RESERVE = 40 * 1024**2
_uncertain_targets = []
PATH_STAGES = (
    ('vertex', '10c381881b80a893ae937b6b3459a73792625d4f2be08a3ecb1e82577fbdea33'),
    ('fragment', '5402a3be2ad74a77df3eb9ad6b550866e44a2832e7652899793ad00cf2103d4d'),
    ('geometry', '810d260d7be1374279302f5a4975c334f432c3d8d4e55dc51e627d2c3d3c74fb'),
)
SHADOW_STAGES = (
    ('vertex', 'ed698f12550e3a11d072e368d6cdb1e34f80a89a202cbcae3385610fed37d0f4'),
    ('fragment', 'c0691d0af0ecc8d25fb508d4fd43791615b661a688723d42f5e21d5fec4f1fd4'),
    ('geometry', 'ed6bcaba87c8f241a542ea7feb397f87179e02618df7ed67a330ef8880203318'),
)
LIGHT_STAGES = (PATH_STAGES[0],
    ('geometry', 'c529c0d345836a6a616703ebb6e2a63afd486afe28b799ced40f820db35a8ad9'),
    ('fragment', 'edd7ee459012e360a6f3289bd68fb6f83bf6c9d5de358695c89e27935300a4e7'))
PATH_BINDINGS = (('u_modelMatrix', 'model_matrix'), ('u_viewMatrix', 'view_matrix'),
    ('u_projectionMatrix', 'projection_matrix'), ('u_normalMatrix', 'normal_matrix'),
    ('u_lightPosition', 'light_0_position'))


def _recipe(recipe, kind='paths'):
    stages = []
    for stage, source in recipe.stages:
        body = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', source, flags=re.S)
        stages.append((stage, hashlib.sha256(re.sub(r'\s+', '', body).encode()).hexdigest()))
    expected = {'paths': PATH_STAGES, 'shadow': SHADOW_STAGES, 'light': LIGHT_STAGES}[kind]
    bindings = PATH_BINDINGS+(('u_viewPosition', 'view_position'),) if kind == 'light' else PATH_BINDINGS
    if (len(stages) != len(expected) or dict(stages) != dict(expected)
            or len(recipe.bindings) != len(bindings) or dict(recipe.bindings) != dict(bindings)):
        raise ValueError('Original native path shader semantics differ')


class RecoveryGeometry:
    def __init__(self, paths, radiance, plates, key):
        self.paths, self.radiance, self.plates = paths, radiance, plates
        self.epoch = paths.epoch
        self.key = key, paths.key, radiance.key, plates.certificate
        self.incremental_bytes = paths.incremental_bytes + plates.retained_bytes
        self.retained_bytes = paths.source_bytes+plates.source_bytes+self.incremental_bytes

    @contextmanager
    def read(self, gl, context, epoch):
        try:
            with self.paths.read(gl, context, epoch): yield self
        except GeometryUncertain as error:
            raise GeometryUncertain(self, str(error)) from error

    def apply(self, shader):
        source, prefix = self.paths.inputs, self.paths.prefix
        values = dict(mpf_path_nodes=8, mpf_pathVertices=9, mpf_pathIndices=10, mpf_path_aliasLines=11,
            mpf_path_nodeCount=len(self.paths.prepared.nodes), mpf_path_sourceLineCount=len(source.indices),
            mpf_pathVertexCount=len(source.mesh.vertices), mpf_pathScalarCount=source.scalar_count,
            mpf_path_first=prefix.first, mpf_path_historyEnd=prefix.history, mpf_path_completed=prefix.completed,
            mpf_path_aliasCount=len(prefix.aliases), mpf_pathClipEnabled=int(prefix.partial is not None),
            mpf_pathLast=prefix.partial[0] if prefix.partial else (0., 0., 0.),
            mpf_pathNext=prefix.partial[1] if prefix.partial else (0., 0., 0.),
            mpf_pathRatio=prefix.partial[2] if prefix.partial else 0.,
            mpf_recoveryPlates=int(bool(len(self.plates.nodes))))
        if len(self.plates.nodes):
            values['mpf_recoveryPlateLow'] = self.plates.nodes[0, :3].tolist()
            values['mpf_recoveryPlateHigh'] = self.plates.nodes[0, 4:7].tolist()
        for name, value in values.items():
            shader.setUniformValue(name, list(value) if type(value) is tuple else value)
        for name, value in self.radiance.uniforms:
            shader.setUniformValue(name, thaw_uniform(value))
        # Publication is last: any failed upload leaves the shader disabled.
        shader.setUniformValue('mpf_recoveryEnabled', 1)

    def close(self):
        try: self.paths.close()
        except Exception as error: raise GeometryUncertain(self, str(error)) from error
        self.plates = self.radiance = None


def create_recovery(gl, context, job, previous, cancel):
    """Clean optional refusal leaves map capture active; cancellation propagates."""
    paths = None
    try:
        frame = job.frame
        if len(frame.paths) != 1: return None
        recipes = dict(frame.recipes)
        recipe = recipes[('paths', False, False)]
        _recipe(recipe)
        mesh, lease, model, bounds, partial, shadow, top = frame.paths[0]
        generation = job.generation, job.serial
        source = admit_path_inputs(mesh, lease, generation, cancel=cancel)
        if not source.indices.size: return None
        prefix = freeze_path_prefix(source, bounds[0], shadow, bounds[1],
            _plain(partial) if partial is not None else None, cancel=cancel)
        if prefix.history > prefix.first: _recipe(recipes[('paths', False, True)], 'shadow')
        radiance = freeze_path_radiance(source, prefix, model=model,
            uniforms=frame.uniforms, defaults=recipe.defaults, lighting=frame.lighting,
            light_effects=frame.light_effects, origin=frame.origin, camera_light=frame.light,
            top_element=top, cancel=cancel)
        if dict(radiance.uniforms)['mpf_pathLightModels'].value:
            _recipe(recipes[('light-path', False)], 'light')
        # Fixed reserve covers both six-face mip pairs and the single face.
        # Private capture uploads/receiver normals and platform texture scale
        # with this frame, so reserve them separately from retained CPU data.
        existing = CAPTURE_RESERVE+sum(owner.retained_bytes for owner in previous)+frame.capture_storage_bytes()
        plates = prepare_plate_source(frame.plates,
            tuple((key, value.stages, value.bindings) for key, value in frame.recipes
                  if type(key) is str and key in ('default', 'platform', 'grid')),
            generation, byte_budget=BUDGET,
            retained_bytes=existing+frame.retained_source_bytes(), cancel=cancel)
        # Original plate arrays already appear in the frame's reservation.
        # Extra duplicated charges are conservative, never omitted owners.
        paths = PathGeometry(gl, context, source, prefix, np.asarray(_plain(model), np.float32).reshape(4, 4),
            epoch=job.generation, source_ready=job.ready_fence, byte_budget=BUDGET,
            existing_bytes=existing+frame.retained_source_bytes()+plates.retained_bytes, group=1, cancel=cancel)
        return RecoveryGeometry(paths, radiance, plates, job.key)
    except GeometryUncertain:
        raise
    except (ValueError, MemoryError, KeyError, RuntimeError) as error:
        if paths is not None: paths.close()
        if cancel(): raise RuntimeError('Cancelled environment recovery') from error
        return None


def recovery_fragment(source):
    """Core-only owned variant; the original map function remains fallback."""
    if '#version 410' not in source: return source
    if source.count('vec3 environmentReflection(') != 1:
        raise ValueError('Unique environment reflection function required')
    source = source.replace('vec3 environmentReflection(', 'vec3 mapEnvironmentReflection(', 1)
    resources = (plugin_path('toolhead', 'environment-path-query.glsl'),
        plugin_path('toolhead', 'environment-path-source.glsl'),
        plugin_path('toolhead', 'environment-path-shade.glsl'),
        plugin_path('toolhead', 'environment-path-light.glsl'),
        plugin_path('toolhead', 'environment-path-compose.glsl'))
    includes = []
    for path in resources:
        with open(path, encoding='utf-8') as stream:
            includes.append(stream.read())
    includes = '\n'.join(includes)
    with open(plugin_path('toolhead', 'environment-recovery.glsl'), encoding='utf-8') as stream:
        includes += '\n'+stream.read()
    anchor = 'float grainHash('
    if source.count(anchor) != 1: raise ValueError('Unique receiver shader insertion point required')
    return source.replace(anchor, includes+'\n'+anchor, 1)


def create_recovery_shader(*, opaque=False):
    from UM.View.GL.ShaderProgram import ShaderProgram
    class RecoveryShader(ShaderProgram):
        def setFragmentShader(self, source):
            if opaque: source = source.replace('if (v_color.a <= 0.0) discard;', '')
            return super().setFragmentShader(recovery_fragment(source))
    shader = RecoveryShader()
    shader.load(plugin_path('toolhead', 'toolhead.shader'), version='41core')
    shader.setUniformValue('mpf_recoveryEnabled', 0)
    return shader


def receiver_fragment(source, *, single_pass=False):
    """Experimental S1 opaque recorder; never substitutes a glass receiver.

    Keep the original derivative-based normal and finish precedence. Its host
    must replay the original viewport, seeded depth and LEQUAL draw ordering.
    Ordinary Cura does not create or bind this program.
    """
    if '#version 410' not in source:
        raise ValueError('Core receiver required')
    main = 'void main() {'
    output = 'out vec4 frag_color;'
    if source.count(main) != 1 or source.count(output) != 1:
        raise ValueError('Unique receiver main and output required')
    rough_start = 'float roughness = clamp(v_material.x, 0.04, 1.0);'
    rough_end = 'float shininess = '
    if source.count(rough_start) != 1 or source.count(rough_end) != 1:
        raise ValueError('Original roughness precedence required')
    roughness = source[source.index(rough_start):source.index(rough_end)]
    # The complete original helper definitions remain byte-identical. Unused
    # map/PBR helpers are eliminated, and no path query is in this program.
    declarations = source[:source.index(main)].replace(output,
        'layout(location=0) out vec4 mpf_receiverOrigin;\n'
        'layout(location=1) out vec4 mpf_receiverRay;')
    discard = '' if single_pass else 'if (v_color.a <= 0.0) discard;'
    return declarations+main+'\n'+discard+'''
        if (u_depthOnly == 1 || u_materialEditEnabled == 1
            || u_lightingEnabled == 0 || u_environmentEnabled != 1) discard;
        vec3 normal = surfaceNormal();
        vec3 eye = normalize(u_orthographic == 1 ? u_viewDirection : u_viewPosition - v_position);
    '''+roughness+'''
        mpf_receiverOrigin = vec4(v_position, 1.0);
        mpf_receiverRay = vec4(reflect(-eye, normal), roughness);
    }
    '''


def recovery_query_fragment():
    """Standalone query program; no CAD shading/receiver functions included."""
    resources = (plugin_path('toolhead', 'environment-path-query.glsl'),
        plugin_path('toolhead', 'environment-path-source.glsl'),
        plugin_path('toolhead', 'environment-path-shade.glsl'),
        plugin_path('toolhead', 'environment-path-light.glsl'),
        plugin_path('toolhead', 'environment-path-compose.glsl'),
        plugin_path('toolhead', 'environment-recovery.glsl'))
    includes = []
    for path in resources:
        with open(path, encoding='utf-8') as stream:
            includes.append(stream.read())
    # A refused pixel publishes invalidity, not the old map's radiance. The
    # ordinary PBR consumer evaluates its original fallback at that pixel.
    header = '''#version 410
uniform sampler2D mpf_receiverOriginTexture,mpf_receiverRayTexture;
uniform float u_probeFar;
out vec4 mpf_recoveredColour;
vec3 v_position;
vec3 mapEnvironmentReflection(vec3 direction,float roughness,out float confidence){
 confidence=0.;return vec3(0.);
}
'''
    with open(plugin_path('toolhead', 'environment-recovery-query.glsl'), encoding='utf-8') as stream:
        return header+'\n'.join(includes)+'\n'+stream.read()


_LAYER_INPUTS = '''
uniform sampler2D mpf_previousCursor,mpf_selectedDepth,mpf_selectedIdentity;
uniform ivec2 mpf_layerOrigin,mpf_layerSize;
uniform int mpf_layerPrevious,mpf_layerBase,mpf_layerCount;
bool mpf_layerFault;
bool mpf_layerFinite(vec4 value){return !any(isnan(value))&&!any(isinf(value));}
bool mpf_layerPixel(out ivec2 pixel){
 pixel=ivec2(floor(gl_FragCoord.xy))-mpf_layerOrigin;
 if(isnan(gl_FragCoord.z)||isinf(gl_FragCoord.z)||gl_FragCoord.z<0.||gl_FragCoord.z>1.
   ||any(lessThanEqual(mpf_layerSize,ivec2(0)))||any(lessThan(pixel,ivec2(0)))
   ||any(greaterThanEqual(pixel,mpf_layerSize))
   ||any(notEqual(textureSize(mpf_previousCursor,0),mpf_layerSize))){mpf_layerFault=true;return false;}
 return true;
}
float mpf_layerID(){
 if(mpf_layerCount<=0||mpf_layerCount>16777216||mpf_layerBase<0
   ||mpf_layerBase>16777216-mpf_layerCount||gl_PrimitiveID<0
   ||gl_PrimitiveID>=mpf_layerCount){mpf_layerFault=true;return -1.;}
 return float(mpf_layerBase+gl_PrimitiveID);
}
bool mpf_layerAfter(ivec2 pixel,float id){
 if(mpf_layerFault)return false;
 if(mpf_layerPrevious==0)return true;
 if(mpf_layerPrevious!=1||any(notEqual(textureSize(mpf_previousCursor,0),mpf_layerSize))){
  mpf_layerFault=true;return false;
 }
 vec4 cursor=texelFetch(mpf_previousCursor,pixel,0);
 if(!mpf_layerFinite(cursor)){mpf_layerFault=true;return false;}
 if(cursor.w==0.){
  if(any(notEqual(cursor,vec4(0.))))mpf_layerFault=true;
  return false;
 }
 if(cursor.w!=1.||cursor.z!=0.||cursor.x<0.||cursor.x>1.||cursor.y<0.
   ||cursor.y>16777215.||cursor.y!=floor(cursor.y)){mpf_layerFault=true;return false;}
 return gl_FragCoord.z>cursor.x||(gl_FragCoord.z==cursor.x&&id>cursor.y);
}
float mpf_layerSelected(sampler2D target,ivec2 pixel){
 if(mpf_layerFault)return -1.;
 if(any(lessThan(pixel,ivec2(0)))||any(greaterThanEqual(pixel,mpf_layerSize))
   ||any(notEqual(textureSize(target,0),mpf_layerSize))){mpf_layerFault=true;return -1.;}
 float value=texelFetch(target,pixel,0).r;
 if(isnan(value)||isinf(value)){mpf_layerFault=true;return -1.;}return value;
}
'''


def layer_vertex(source):
    """Identical original position evaluation across the owned layer passes."""
    if '#version 410' not in source or source.count('void main() {') != 1:
        raise ValueError('Unique original core vertex main required')
    if source.count('gl_Position = u_projectionMatrix * u_viewMatrix * world;') != 1:
        raise ValueError('Original camera position evaluation required')
    return source.replace('#version 410', '#version 410\ninvariant gl_Position;', 1)


def _layer_inputs(samples):
    """GL4.1 has no textureSamples; the owner must certify actual MS storage.

    mpf_layerSamples is delivery of that certificate, not discovery or proof of
    a target's sample count. All input/target counts and ordered sample positions
    must have matching native receipts before invoking the four-plane programs.
    """
    if type(samples) is not int or samples not in (1, 4):
        raise ValueError('Original single or four sample storage required')
    if samples == 1: return _LAYER_INPUTS
    # Fetch every original plane without sample-frequency interpolation. The
    # output mask is applied only after the original fragment computations.
    result = (_LAYER_INPUTS.replace('sampler2D', 'sampler2DMS')
        .replace('bool mpf_layerFault;',
                 'bool mpf_layerFault;int mpf_layerSample;uniform int mpf_layerSamples;')
        .replace('textureSize(mpf_previousCursor,0)', 'textureSize(mpf_previousCursor)')
        .replace('textureSize(target,0)', 'textureSize(target)')
        .replace('texelFetch(mpf_previousCursor,pixel,0)',
                 'texelFetch(mpf_previousCursor,pixel,mpf_layerSample)')
        .replace('texelFetch(target,pixel,0)', 'texelFetch(target,pixel,mpf_layerSample)')
        .replace('||any(notEqual(textureSize(mpf_previousCursor),mpf_layerSize))',
                 '||mpf_layerSamples!=4'
                 '||any(notEqual(textureSize(mpf_previousCursor),mpf_layerSize))')
        .replace('||any(notEqual(textureSize(target),mpf_layerSize))',
                 '||mpf_layerSamples!=4||any(notEqual(textureSize(target),mpf_layerSize))'))
    # Pixel-frequency depth can extrapolate outside clip depth at a partially
    # covered edge. It is an enumeration rank, not the hardware visibility
    # depth. Retain its exact finite value; carry selector state separately.
    result=result.replace('||gl_FragCoord.z<0.||gl_FragCoord.z>1.', '')
    result=result.replace('||cursor.x<0.||cursor.x>1.', '')
    return result+"""
vec2 mpf_layerDepth(ivec2 pixel){
 float rank=mpf_layerSelected(mpf_selectedDepth,pixel);
 if(mpf_layerFault)return vec2(0.,-1.);
 float state=texelFetch(mpf_selectedDepth,pixel,mpf_layerSample).g;
 if(isnan(state)||isinf(state)){mpf_layerFault=true;return vec2(0.,-1.);}
 return vec2(rank,state);
}
"""



def layer_selection_fragment(stage, *, single_pass=False, samples=1):
    """Unwired scalar GL_MIN selectors; hardware depth is visibility only.

    Depth and identity require distinct R32F targets/passes, unchanged original
    static depth, and no depth writes. Negative values refuse the whole build;
    depth2/identity16777216 are empty sentinels, never valid receivers.
    Four-plane output masks intersect original coverage after interpolation;
    they require certified S4 storage, with sample shading/coverage controls off.
    """
    if stage not in ('depth', 'identity'):
        raise ValueError('Separate depth or identity layer selector required')
    inputs = _layer_inputs(samples)
    discard = '' if single_pass else 'if(v_color.a<=0.)discard;'
    if samples == 4:
        selection = '' if stage == 'depth' else """
  vec2 pair=mpf_layerDepth(pixel);float depth=pair.x;
  if(pair.y==1.&&depth==uintBitsToFloat(0x7f7fffffu))eligible=false;
  else if(pair.y!=0.)mpf_layerFault=true;
  else if(gl_FragCoord.z!=depth)eligible=false;
"""
        state = 'fault?-1.:0.' if stage=='depth' else '0.'
        rank = 'fault?uintBitsToFloat(0x7f7fffffu):gl_FragCoord.z' if stage=='depth' else 'fault?-1.:id'
        return '#version 410\nin vec4 v_color;out vec4 frag_color;\n'+inputs+'''
void main(){
 '''+discard+'''
 int mask=0;bool fault=false;float id=-1.;
 for(int plane=0;plane<4;++plane){
  mpf_layerSample=plane;mpf_layerFault=false;ivec2 pixel;id=mpf_layerID();
  bool eligible=mpf_layerPixel(pixel)&&mpf_layerAfter(pixel,id);
 '''+selection+'''
  if(eligible||mpf_layerFault)mask|=1<<plane;
  fault=fault||mpf_layerFault;
 }
 if(mask==0)discard;
 gl_SampleMask[0]=mask;
 frag_color=vec4('''+rank+''','''+state+''',0.,1.);
}
'''
    select = 'float value=gl_FragCoord.z;'
    if stage == 'identity':
        select = '''float depth=mpf_layerSelected(mpf_selectedDepth,pixel);
 if(!mpf_layerFault&&depth==2.)discard;
 if(depth<0.||depth>1.)mpf_layerFault=true;
 if(!mpf_layerFault&&gl_FragCoord.z!=depth)discard;
 float value=id;'''
    return '#version 410\nin vec4 v_color;out vec4 frag_color;\n'+inputs+'''
void main(){
 '''+discard+'''
 mpf_layerFault=false;ivec2 pixel;float id=mpf_layerID();
 bool eligible=mpf_layerPixel(pixel)&&mpf_layerAfter(pixel,id);
 if(!eligible&&!mpf_layerFault)discard;
 '''+select+'''
 frag_color=vec4(mpf_layerFault?-1.:value,0.,0.,1.);
}
'''


def layer_receiver_fragment(source, *, single_pass=False, samples=1):
    """Exact selected primitive payload; original derivatives precede peeling."""
    recorded = receiver_fragment(source, single_pass=single_pass)
    inputs = _layer_inputs(samples)
    anchor = 'mpf_receiverOrigin = vec4(v_position, 1.0);'
    if recorded.count(anchor) != 1:
        raise ValueError('Unique original receiver payload required')
    recorded = recorded.replace('void main() {', inputs+
        'layout(location=2) out vec4 mpf_receiverIdentity;\nvoid main() {', 1)
    if samples == 4:
        gate = '''int mask=0;bool fault=false;float id=-1.;
        for(int plane=0;plane<4;++plane){
            mpf_layerSample=plane;mpf_layerFault=false;ivec2 pixel;id=mpf_layerID();
            bool eligible=mpf_layerPixel(pixel)&&mpf_layerAfter(pixel,id);
            vec2 pair=mpf_layerDepth(pixel);float depth=pair.x;
            float selected=mpf_layerSelected(mpf_selectedIdentity,pixel);
            bool empty=pair.y==1.&&depth==uintBitsToFloat(0x7f7fffffu)&&selected==16777216.;
            bool valid=pair.y==0.&&selected>=0.&&selected<=16777215.
                &&selected==floor(selected);
            if(!empty&&!valid)mpf_layerFault=true;
            bool match=!empty&&eligible&&gl_FragCoord.z==depth&&id==selected;
            if(match||mpf_layerFault)mask|=1<<plane;
            fault=fault||mpf_layerFault;
        }
        if(mask==0)discard;
        gl_SampleMask[0]=mask;
        if(fault){
            mpf_receiverOrigin=vec4(0.);mpf_receiverRay=vec4(0.);
            mpf_receiverIdentity=vec4(-1.,-1.,0.,-1.);return;
        }
        mpf_receiverIdentity=vec4(gl_FragCoord.z,id,0.,1.);
        '''
        return recorded.replace(anchor, gate+anchor, 1)
    gate = '''mpf_layerFault=false;ivec2 pixel;float id=mpf_layerID();
        bool eligible=mpf_layerPixel(pixel)&&mpf_layerAfter(pixel,id);
        float depth=mpf_layerSelected(mpf_selectedDepth,pixel);
        float selected=mpf_layerSelected(mpf_selectedIdentity,pixel);
        bool empty=depth==2.&&selected==16777216.;
        bool valid=depth>=0.&&depth<=1.&&selected>=0.&&selected<=16777215.
            &&selected==floor(selected);
        if(!empty&&!valid)mpf_layerFault=true;
        if(mpf_layerFault){
            mpf_receiverOrigin=vec4(0.);mpf_receiverRay=vec4(0.);
            mpf_receiverIdentity=vec4(-1.,-1.,0.,-1.);return;
        }
        if(empty||!eligible||gl_FragCoord.z!=depth||id!=selected)discard;
        mpf_receiverIdentity=vec4(gl_FragCoord.z,id,0.,1.);
        '''
    return recorded.replace(anchor, gate+anchor, 1)


def recovery_lookup_fragment(source):
    """Owned PBR lookup variant; carries no geometry traversal or path samplers."""
    if '#version 410' not in source:
        raise ValueError('Core lookup required')
    function, anchor = 'vec3 environmentReflection(', 'float grainHash('
    if source.count(function) != 1 or source.count(anchor) != 1:
        raise ValueError('Unique map fallback and lookup insertion required')
    source = source.replace(function, 'vec3 mapEnvironmentReflection(', 1)
    with open(plugin_path('toolhead', 'environment-recovery-lookup.glsl'), encoding='utf-8') as stream:
        return source.replace(anchor, stream.read()+'\n'+anchor, 1)


def layer_lookup_fragment(source, *, samples=1):
    """Completed primitive-ID lookup; original PBR/map fallback stays intact."""
    if '#version 410' not in source or type(samples) is not int or samples not in (1,4):
        raise ValueError('Core layered lookup required')
    function, anchor = 'vec3 environmentReflection(', 'float grainHash('
    if source.count(function) != 1 or source.count(anchor) != 1:
        raise ValueError('Unique original map function and insertion point required')
    source = source.replace(function, 'vec3 mapEnvironmentReflection(', 1)
    with open(plugin_path('toolhead', 'environment-layer-lookup.glsl'), encoding='utf-8') as stream:
        source=source.replace(anchor, stream.read()+'\n'+anchor, 1)
    if samples==1: return source
    pattern=r'\bvoid\s+main\s*\(\s*\)\s*\{'
    if len(re.findall(pattern,source))!=1: raise ValueError('Unique original PBR main required')
    # Every original colour return must reach the plane mask, including unlit
    # and material-edit branches. Calling the intact original body preserves
    # derivative/PBR evaluation before narrowing; depth-only keeps all planes.
    if 'uniform int u_depthOnly;' not in source or 'mpf_layerOriginalMain' in source:
        raise ValueError('Original depth-only contract required')
    source=re.sub(pattern,'void mpf_layerOriginalMain() {',source,count=1)
    return source+'''
void main(){
 gl_SampleMask[0]=-1;
 mpf_layerOriginalMain();
 if(u_depthOnly==1)return;
 if(mpf_layerSampleCount!=4||mpf_layerLookupPlane<0||mpf_layerLookupPlane>=4)discard;
 gl_SampleMask[0]=1<<mpf_layerLookupPlane;
}
'''


class RecoveryTargets:
    """Unwired exact-context S1 receiver records and completed radiance image.

    The caller supplies the *whole* retained graphics/source ledger and the
    exact camera/depth/receiver/cohort key. This owner does not discover scene
    inputs or grant glass, multisample or shutter eligibility. Query submissions
    join the caller's completed-map ticket; no GL binding survives a turn.
    """
    def __init__(self, gl, context, width, height, *, retained_bytes, byte_budget):
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        from PyQt6.QtCore import Qt
        if (any(type(value) is not int for value in (width, height, retained_bytes, byte_budget))
                or not 0 < width <= 8192 or not 0 < height <= 8192 or retained_bytes < 0):
            raise ValueError('Explicit bounded recovery storage required')
        self.retained_bytes = width*height*52  # Two RGBA32F + depth<=4B + result RGBA32F.
        if retained_bytes+self.retained_bytes > byte_budget:
            raise MemoryError('Combined environment receiver storage exceeds budget')
        if (QOpenGLContext.currentContext() is not context or context.format().majorVersion() < 4):
            raise RuntimeError('Recovery targets require their exact core4 context')
        self.gl, self.context, self.width, self.height = gl, context, width, height
        self.records = self.image = None
        self.key = self.source_key = self.ready_key = None
        self.tiles = range(0); self.next_tile = 0
        self._write = self._read = None
        self._readers = self._submissions = 0; self.closed = self.quarantined = False
        self._retirement = None
        try:
            with preserved_state(gl, context):
                fmt = QOpenGLFramebufferObjectFormat()
                fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
                fmt.setInternalTextureFormat(0x8814)
                self.records = QOpenGLFramebufferObject(width, height, fmt)
                self.records.addColorAttachment(width, height, 0x8814)
                fmt = QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(0x8814)
                self.image = QOpenGLFramebufferObject(width, height, fmt)
                self._certify()
        except Exception as error:
            # Even successful allocation followed by failed restoration must
            # retain the wrappers. Caller cannot silently continue host drawing.
            self._uncertain(error)
        owner_ref = ref(self)
        def destroyed():
            owner = owner_ref()
            if owner is not None:
                try: owner.close()
                except Exception: pass  # close retains the uncertain owner.
        self._retirement = destroyed
        context.aboutToBeDestroyed.connect(destroyed, Qt.ConnectionType.DirectConnection)

    def _uncertain(self, error):
        self.quarantined = True; self.ready_key = self.key = None
        if not any(owner is self for owner in _uncertain_targets): _uncertain_targets.append(self)
        raise GeometryUncertain(self, str(error)) from error

    def _current(self):
        from PyQt6.QtGui import QOpenGLContext
        if self.closed or self.quarantined or QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Recovery requires its live creating context')

    def _certify(self):
        gl = self.gl
        query = procedure(self.context, 'glGetTexLevelParameteriv', None,
            ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))
        depth = procedure(self.context, 'glGetFramebufferAttachmentParameteriv', None,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))
        for target, count in ((self.records, 2), (self.image, 1)):
            if not target.isValid() or not target.bind() or len(target.textures()) != count:
                raise RuntimeError('Recovery attachments unavailable')
            if int(gl.glGetIntegerv(0x80A9)) != 0:
                raise RuntimeError('Recovery prototype requires actual single-sample storage')
            for texture in target.textures():
                gl.glActiveTexture(0x84C0); gl.glBindTexture(0x0DE1, texture)
                for parameter, expected in ((0x1003, 0x8814), (0x1000, self.width), (0x1001, self.height)):
                    value = ctypes.c_int(); query(0x0DE1, 0, parameter, ctypes.byref(value))
                    if value.value != expected: raise RuntimeError('Recovery storage descriptor differs')
        self.records.bind(); bits = ctypes.c_int()
        depth(0x8D40, 0x8D00, 0x8216, ctypes.byref(bits))
        if bits.value not in (16, 24, 32) or gl.glGetError():
            raise RuntimeError('Recovery depth storage is uncertified')

    def _fence(self):
        self._current()
        value = procedure(self.context, 'glFenceSync', ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)(0x9117, 0)
        if not value: raise RuntimeError('Recovery completion fence unavailable')
        self.gl.glFlush(); return int(value)

    def _poll(self, name):
        self._current()
        value = getattr(self, name)
        if value is None: return True
        status = procedure(self.context, 'glClientWaitSync', ctypes.c_uint,
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_ulonglong)(ctypes.c_void_p(value), 0, 0)
        if status == 0x911B: return False
        if status not in (0x911A, 0x911C): raise RuntimeError('Recovery GPU completion is uncertain')
        procedure(self.context, 'glDeleteSync', None, ctypes.c_void_p)(ctypes.c_void_p(value))
        setattr(self, name, None); return True

    def invalidate(self):
        self.key = self.source_key = self.ready_key = None
        self.tiles = range(0); self.next_tile = 0

    def tile_rect(self, index):
        """Constant storage; no uncharged crop-sized tuple of tile metadata."""
        if index not in self.tiles: raise IndexError('Recovery tile unavailable')
        row, column = divmod(index, (self.width+15)//16)
        x, y = column*16, row*16
        return x, y, min(16, self.width-x), min(16, self.height-y)

    @contextmanager
    def _submission(self):
        self._submissions += 1
        try: yield
        finally: self._submissions -= 1

    def capture(self, key, source_key, camera, viewport, crop, cropped_camera, *, seed, draw):
        """Require exact scene-depth replay and original opaque receiver draw."""
        self._current()
        if self._submissions: return False
        if key is None or source_key is None or not callable(seed) or not callable(draw):
            raise ValueError('Exact receiver/cohort/depth inputs required')
        if self.key != key: self.invalidate()
        try:
            if self._readers or not self._poll('_write') or not self._poll('_read'): return False
            self.invalidate()
            with self._submission(), preserved_state(self.gl, self.context):
                self._bind_records()
                self.gl.glClearColor(0., 0., 0., 0.); self.gl.glDepthMask(True); self.gl.glClearDepth(1.)
                self.gl.glClear(self.gl.GL_COLOR_BUFFER_BIT | self.gl.GL_DEPTH_BUFFER_BIT)
                if seed(self.gl, self.records, camera, viewport, crop, cropped_camera) is not True:
                    return False  # No unseeded recovery or partially valid generation.
                self._bind_records()
                self.gl.glDepthMask(True); self.gl.glDepthFunc(self.gl.GL_LEQUAL)
                self.gl.glEnable(self.gl.GL_DEPTH_TEST); self.gl.glDisable(self.gl.GL_BLEND)
                draw(cropped_camera)
                if self.gl.glGetError(): raise RuntimeError('Receiver capture failed')
                self._write = self._fence()
            self.key, self.source_key = key, source_key
            self.tiles = range(((self.width+15)//16)*((self.height+15)//16))
            return True
        except Exception as error: self._uncertain(error)

    def _bind_records(self):
        self._current()
        if not self.records.bind(): raise RuntimeError('Receiver target bind failed')
        procedure(self.context, 'glDrawBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(
            2, (ctypes.c_uint*2)(0x8CE0, 0x8CE1))
        self.gl.glViewport(0, 0, self.width, self.height)
        self.gl.glDisable(self.gl.GL_SCISSOR_TEST); self.gl.glColorMask(True, True, True, True)

    def step(self, selected_key, *, query_scope, draw):
        """At most one pending tile; no partially completed image publication."""
        self._current()
        if self._submissions: return False
        if selected_key != self.key: self.invalidate(); return False
        if self.key is None: return False
        try:
            if not self._poll('_write'): return False
            if self.next_tile == len(self.tiles): self.ready_key = self.key; return True
            with self._submission(), preserved_state(self.gl, self.context), query_scope() as geometry:
                self._current()
                if geometry is None or geometry.key != self.source_key:
                    self.invalidate(); return False
                if not self.image.bind(): raise RuntimeError('Recovery output bind failed')
                self.gl.glViewport(0, 0, self.width, self.height)
                for flag in (self.gl.GL_BLEND, self.gl.GL_DEPTH_TEST, self.gl.GL_CULL_FACE, self.gl.GL_SCISSOR_TEST):
                    self.gl.glDisable(flag)
                self.gl.glColorMask(True, True, True, True)
                if self.next_tile == 0:
                    self.gl.glClearColor(0., 0., 0., 0.); self.gl.glClear(self.gl.GL_COLOR_BUFFER_BIT)
                self.gl.glEnable(self.gl.GL_SCISSOR_TEST); self.gl.glScissor(*self.tile_rect(self.next_tile))
                draw(geometry, tuple(self.records.textures()))
                if self.gl.glGetError(): raise RuntimeError('Recovery query submission failed')
                self._write = self._fence()
            self.next_tile += 1
            return False
        except Exception as error: self._uncertain(error)

    @contextmanager
    def read(self, selected_key):
        self._current()
        if selected_key is None or self.ready_key != selected_key:
            yield None; return
        self._readers += 1
        try: yield self.image.texture()
        finally:
            self._readers -= 1
            try:
                # One later same-context fence covers all earlier PBR uses.
                previous, self._read = self._read, self._fence()
                if previous is not None:
                    procedure(self.context, 'glDeleteSync', None, ctypes.c_void_p)(ctypes.c_void_p(previous))
            except Exception as error: self._uncertain(error)

    def close(self):
        if self.closed: return
        try:
            self._current()
            if self._readers or self._submissions:
                raise RuntimeError('Recovery targets still have admitted readers or submissions')
            self.invalidate(); self.gl.glFinish()
            if self.gl.glGetError() or not self._poll('_write') or not self._poll('_read'):
                raise RuntimeError('Recovery teardown GPU completion is uncertain')
            self.records = self.image = None
            if self._retirement is not None:
                self.context.aboutToBeDestroyed.disconnect(self._retirement)
                self._retirement = None
            self.closed = True
        except Exception as error: self._uncertain(error)
