"""Private context GL calls and programs for asynchronous reflection capture.

No native renderer, global OpenGL singleton or host resource wrapper is used.
The ShaderProgram value/binding adapter is retained; its Qt GL program is
replaced per owned instance, without changing the native class or globals.
"""
from __future__ import annotations
import ctypes
import struct
from PyQt6.QtGui import QOpenGLContext, QMatrix4x4, QVector2D, QVector3D, QVector4D, QColor
from UM.View.GL.ShaderProgram import ShaderProgram
from .ToolheadGLState import procedure

class RawBindings:
 def __init__(self,context,constants):
  self.context=context;self.constants=dict(constants);self.calls={}
  for name,value in self.constants.items():
   if not name.startswith('GL_'):raise ValueError('Invalid graphics constant')
   setattr(self,name,value)
  for name in ('glActiveTexture','glBindTexture','glBindBuffer','glClear','glClearColor','glClearDepth',
    'glColorMask','glDepthMask','glDepthFunc','glEnable','glDisable','glCullFace','glFrontFace',
    'glBlendFunc','glBlendFuncSeparate','glBlendEquation','glBlendEquationSeparate','glBlendColor',
    'glPolygonOffset','glViewport','glScissor','glTexParameteri','glGetError','glIsEnabled',
    'glDrawElements','glDrawRangeElements','glDrawArrays','glFlush','glFinish',
    'glGetIntegerv','glGetBooleanv','glGetFloatv','glGetDoublev'):
   setattr(self,name,self._resolve(name))
 def _resolve(self,name):
  if name in self.calls:return self.calls[name]
  U,I,F,D,B,P=ctypes.c_uint,ctypes.c_int,ctypes.c_float,ctypes.c_double,ctypes.c_ubyte,ctypes.c_void_p
  signatures={
   'glActiveTexture':(None,U),'glBindTexture':(None,U,U),'glBindBuffer':(None,U,U),
   'glClear':(None,U),'glClearColor':(None,F,F,F,F),'glClearDepth':(None,D),
   'glColorMask':(None,B,B,B,B),'glDepthMask':(None,B),'glDepthFunc':(None,U),
   'glEnable':(None,U),'glDisable':(None,U),'glCullFace':(None,U),'glFrontFace':(None,U),
   'glBlendFunc':(None,U,U),'glBlendFuncSeparate':(None,U,U,U,U),
   'glBlendEquation':(None,U),'glBlendEquationSeparate':(None,U,U),
   'glBlendColor':(None,F,F,F,F),'glPolygonOffset':(None,F,F),'glViewport':(None,I,I,I,I),
   'glScissor':(None,I,I,I,I),'glTexParameteri':(None,U,U,I),
   'glGetError':(U,),'glIsEnabled':(B,U),'glDrawElements':(None,U,I,U,P),
   'glDrawRangeElements':(None,U,U,U,I,U,P),'glDrawArrays':(None,U,I,I),
   'glFlush':(None,),'glFinish':(None,)}
  if name in signatures:
   signature=signatures[name];function=procedure(self.context,name,*signature)
  elif name in ('glGetIntegerv','glGetBooleanv','glGetFloatv','glGetDoublev'):
   scalar={'glGetIntegerv':I,'glGetBooleanv':B,'glGetFloatv':F,'glGetDoublev':D}[name]
   raw=procedure(self.context,name,None,U,ctypes.POINTER(scalar))
   def function(parameter):
    length=4 if parameter in (0x0BA2,0x0C10,0x0C23,0x0C22,0x8005) else 1
    result=(scalar*length)();raw(parameter,result)
    return tuple(result) if length>1 else result[0]
  else:raise RuntimeError('Unqualified worker GL API '+name)
  self.calls[name]=function
  return function


class RawProgram:
 def __init__(self):
  self.context=QOpenGLContext.currentContext();self.functions={};self.shaders=[];self.linked=False;self._uniform_cache={}
  self.name=self.fn('glCreateProgram',ctypes.c_uint)();
  if not self.name: raise RuntimeError('Reflection program allocation failed')
 def fn(self,name,result,*args):
  if name not in self.functions:self.functions[name]=procedure(self.context,name,result,*args)
  return self.functions[name]
 def programId(self):return self.name
 def addShaderFromSourceCode(self,kind,source):
  U,I,P=ctypes.c_uint,ctypes.c_int,ctypes.c_char_p
  native_kind={1:0x8B31,2:0x8B30,4:0x8DD9}[kind.value]
  shader=self.fn('glCreateShader',U,U)(native_kind);
  if not shader: raise RuntimeError('Reflection shader allocation failed')
  text=source.encode('utf-8');pointer=P(text);length=I(len(text))
  self.shaders.append(shader)
  self.fn('glShaderSource',None,U,I,ctypes.POINTER(P),ctypes.POINTER(I))(shader,1,ctypes.byref(pointer),ctypes.byref(length))
  self.fn('glCompileShader',None,U)(shader)
  status=I();self.fn('glGetShaderiv',None,U,U,ctypes.POINTER(I))(shader,0x8B81,ctypes.byref(status))
  if not status.value: raise RuntimeError(self.shader_log(shader))
  self.fn('glAttachShader',None,U,U)(self.name,shader)
  return True
 def shader_log(self,shader):
  size=ctypes.c_int();self.fn('glGetShaderiv',None,ctypes.c_uint,ctypes.c_uint,ctypes.POINTER(ctypes.c_int))(shader,0x8B84,ctypes.byref(size))
  out=ctypes.create_string_buffer(max(1,size.value));self.fn('glGetShaderInfoLog',None,ctypes.c_uint,ctypes.c_int,ctypes.POINTER(ctypes.c_int),ctypes.c_void_p)(shader,len(out),None,out)
  return out.value.decode('utf-8',errors='replace')
 def link(self):
  self._uniform_cache.clear()
  self.fn('glLinkProgram',None,ctypes.c_uint)(self.name);status=ctypes.c_int()
  self.fn('glGetProgramiv',None,ctypes.c_uint,ctypes.c_uint,ctypes.POINTER(ctypes.c_int))(self.name,0x8B82,ctypes.byref(status))
  if not status.value: raise RuntimeError(self.log())
  self.linked=True
  return True
 def log(self):
  size=ctypes.c_int();self.fn('glGetProgramiv',None,ctypes.c_uint,ctypes.c_uint,ctypes.POINTER(ctypes.c_int))(self.name,0x8B84,ctypes.byref(size))
  out=ctypes.create_string_buffer(max(1,size.value));self.fn('glGetProgramInfoLog',None,ctypes.c_uint,ctypes.c_int,ctypes.POINTER(ctypes.c_int),ctypes.c_void_p)(self.name,len(out),None,out)
  return out.value.decode('utf-8',errors='replace')
 def isLinked(self):return self.linked
 def bind(self):self.fn('glUseProgram',None,ctypes.c_uint)(self.name);return True
 def release(self):self.fn('glUseProgram',None,ctypes.c_uint)(0)
 def uniformLocation(self,name):return self.fn('glGetUniformLocation',ctypes.c_int,ctypes.c_uint,ctypes.c_char_p)(self.name,name.encode('utf-8'))
 def attributeLocation(self,name):return self.fn('glGetAttribLocation',ctypes.c_int,ctypes.c_uint,ctypes.c_char_p)(self.name,name.encode('utf-8'))
 def setAttributeBuffer(self,location,kind,offset,components,stride):
  self.fn('glVertexAttribPointer',None,ctypes.c_uint,ctypes.c_int,ctypes.c_uint,ctypes.c_ubyte,ctypes.c_int,ctypes.c_void_p)(location,components,kind,0,stride,ctypes.c_void_p(offset))
 def enableAttributeArray(self,location):self.fn('glEnableVertexAttribArray',None,ctypes.c_uint)(location)
 def disableAttributeArray(self,location):self.fn('glDisableVertexAttribArray',None,ctypes.c_uint)(location)
 def setUniformValue(self,location,value):
  I,F=ctypes.c_int,ctypes.c_float
  if isinstance(value,QMatrix4x4):
   body=(F*16)(*value.copyDataTo());key=('matrix',bytes(body))
   function=lambda:self.fn('glUniformMatrix4fv',None,I,I,ctypes.c_ubyte,ctypes.POINTER(F))(location,1,1,body)
  elif isinstance(value,(QVector2D,QVector3D,QVector4D,QColor)):
   if isinstance(value,QColor):values=(value.redF(),value.greenF(),value.blueF(),value.alphaF())
   else:
    n=2 if isinstance(value,QVector2D) else 3 if isinstance(value,QVector3D) else 4
    values=[value.x(),value.y()]+([value.z()] if n>=3 else [])+([value.w()] if n==4 else [])
   n=len(values);key=('vector',struct.pack('='+str(n)+'f',*values))
   function=lambda:self.fn('glUniform'+str(n)+'f',None,I,*([F]*n))(location,*values)
  elif type(value) in (int,bool):
   key=('int',I(int(value)).value);function=lambda:self.fn('glUniform1i',None,I,I)(location,int(value))
  elif type(value) is float:
   key=('float',bytes(F(value)));function=lambda:self.fn('glUniform1f',None,I,F)(location,value)
  else:raise RuntimeError('Unqualified uniform value '+repr(type(value)))
  # This program is private to one capture context. Uniform values survive
  # unbinding; cache their exact delivered representation, not mutable objects.
  if self._uniform_cache.get(location)==key:return
  function();self._uniform_cache[location]=key
 def setUniformValueArray(self,location,values):
  # Bulk arrays can overlap locations set through individual element names.
  # Invalidate before delivery so neither ordering can leave a stale cache.
  self._uniform_cache.clear()
  F=ctypes.c_float
  if values and isinstance(values[0],(QVector2D,QVector3D)):
   n=2 if isinstance(values[0],QVector2D) else 3
   flat=[part for v in values for part in ([v.x(),v.y()] if n==2 else [v.x(),v.y(),v.z()])]
  else:n=1;flat=values
  body=(F*len(flat))(*flat)
  self.fn('glUniform'+str(n)+'fv',None,ctypes.c_int,ctypes.c_int,ctypes.POINTER(F))(location,len(values),body)
 def close(self):
  if not self.name:return
  for shader in self.shaders:self.fn('glDeleteShader',None,ctypes.c_uint)(shader)
  self.fn('glDeleteProgram',None,ctypes.c_uint)(self.name);self.name=0
class RawShaderProgram(ShaderProgram):
 def __init__(self):super().__init__();self._shader_program=RawProgram()
