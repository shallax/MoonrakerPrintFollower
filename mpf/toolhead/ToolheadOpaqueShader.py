"""Use the shared lighting shader without discard for an opaque CAD batch."""
from ..resources.PluginPaths import plugin_path


def create_opaque_shader():
    from UM.View.GL.OpenGLContext import OpenGLContext
    from UM.View.GL.ShaderProgram import ShaderProgram

    class OpaqueShader(ShaderProgram):
        def setFragmentShader(self, source):
            return super().setFragmentShader(source.replace("if (v_color.a <= 0.0) discard;", ""))

    shader = OpaqueShader()
    shader.load(plugin_path("toolhead", "toolhead.shader"),
                version="" if OpenGLContext.isLegacyOpenGL() else "41core")
    return shader
