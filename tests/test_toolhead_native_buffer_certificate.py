"""Native writer qualification is stable across Python AST formatting versions."""
import ast
import copy
import unittest
from unittest.mock import patch

from mpf.cura import ToolheadSimulationPass as module


class NativeBufferCertificateTests(unittest.TestCase):
    def test_only_audited_upload_and_render_pairs_are_admitted(self):
        upload = lambda: None
        render = lambda: None
        for pair in module._NATIVE_WRITER_DIGESTS:
            with self.subTest(pair=pair):
                module._known_native_writer.cache_clear()
                with patch.object(module, '_writer_digest', side_effect=pair):
                    self.assertTrue(module._known_native_writer(upload, render))
                module._known_native_writer.cache_clear()
                with patch.object(module, '_writer_digest', side_effect=(pair[0], 'changed-render')):
                    self.assertFalse(module._known_native_writer(upload, render))
                module._known_native_writer.cache_clear()
                with patch.object(module, '_writer_digest', side_effect=('changed-upload', pair[1])):
                    self.assertFalse(module._known_native_writer(upload, render))
        module._known_native_writer.cache_clear()

    def test_empty_optional_fields_do_not_depend_on_interpreter_ast_shape(self):
        original=ast.parse('def upload(mesh):\n    return mesh.cache\n').body[0]
        older=copy.deepcopy(original)
        # Older runtimes omit newly introduced optional fields (type_params);
        # ast.dump also changed whether all other empty fields are printed.
        for node in ast.walk(older):
            node._fields=tuple(name for name in node._fields
                if getattr(node,name,None) is not None and getattr(node,name,None)!=[])
        self.assertEqual(module._canonical_ast(original),module._canonical_ast(older))
        changed=ast.parse('def upload(mesh):\n    return mesh.other_cache\n').body[0]
        self.assertNotEqual(module._canonical_ast(original),module._canonical_ast(changed))

    def test_writer_digest_ignores_dump_defaults_but_preserves_semantic_changes(self):
        function=lambda:None
        source='def upload(mesh):\n    return mesh.cache\n'
        with patch.object(module.inspect,'getsource',return_value=source):
            expected=module._writer_digest(function)
            for rendering in ('FunctionDef(args=arguments(), body=[])',
                    'FunctionDef(args=arguments(args=[], defaults=[]), decorator_list=[], type_params=[])'):
                with patch.object(ast,'dump',return_value=rendering) as dump:
                    self.assertEqual(module._writer_digest(function),expected)
                    dump.assert_not_called()
        with patch.object(module.inspect,'getsource',return_value=source.replace('mesh.cache','mesh.other_cache')):
            self.assertNotEqual(module._writer_digest(function),expected)

    def test_adapter_requires_exact_host_type_and_both_audited_writers(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from mpf.toolhead import ToolheadPathGeometry as geometry
        class LayerData:pass
        class Derived(LayerData):pass
        writer=lambda:None
        render=lambda:None
        context=object();mesh=LayerData();proof=object()
        api=SimpleNamespace(createVertexBuffer=writer)
        with patch.dict(sys.modules,{'cura.LayerData':SimpleNamespace(LayerData=LayerData),
                'UM.View.GL.OpenGL':SimpleNamespace(OpenGL=api)}), patch.object(
                module,'_known_native_writer',return_value=True) as qualify, patch.object(
                geometry,'observe_native_upload',return_value=proof) as observe:
            self.assertIsNone(module.observe_native_upload(Derived(),context,Mock()))
            qualify.assert_not_called();observe.assert_not_called()
            bound=SimpleNamespace(__func__=render)
            self.assertIs(module.observe_native_upload(mesh,context,bound),proof)
            qualify.assert_called_once_with(writer,render)
            observe.assert_called_once_with(mesh,context,writer,source_type=LayerData)
            qualify.return_value=False;observe.reset_mock()
            self.assertIsNone(module.observe_native_upload(mesh,context,bound))
            observe.assert_not_called()

    def test_metadata_keeps_exact_source_type_in_later_admission_checks(self):
        import numpy as np
        from mpf.toolhead import ToolheadPathGeometry as geometry
        class Source:
            def getVertexCount(self):return 0
            def getVertices(self):return None
            def getNormals(self):return None
            def getUVCoordinates(self):return None
            def attributeNames(self):return ()
            def getIndices(self):return self.indices
        class Replacement(Source):pass
        source=Source();source.indices=np.array([],np.int32);source.indices.flags.writeable=False
        original=geometry._native_signature(source)
        source.__class__=Replacement
        self.assertNotEqual(geometry._native_signature(source),original)
