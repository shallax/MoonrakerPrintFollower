# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Enforce package-qualified core boundaries and detect production import cycles.

"""Layer laws supplement module allowlists, including lazily executed imports."""
import ast
from pathlib import Path
import unittest
from tests.test_domain_layout import imported_modules, module_name

ROOT = Path(__file__).resolve().parents[1]


def production_graph():
    source_root = ROOT / 'mpf'
    modules = {module_name(path, source_root): path for path in source_root.rglob('*.py')}
    graph = {}
    imports = {}
    for module, path in modules.items():
        source = path.read_text()
        graph[module] = imported_modules(source, module, modules, package=path.name == '__init__.py')
        edges = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and not node.level:
                edges.append((node.module, node.lineno))
            elif isinstance(node, ast.Import):
                edges.extend((alias.name, node.lineno) for alias in node.names)
        imports[module] = edges
    return graph, imports


class DomainBoundaryTests(unittest.TestCase):
    def test_parser_and_storage_layers_do_not_import_host_or_application_components(self):
        _graph, imports = production_graph()
        for module, edges in imports.items():
            if not module.startswith(('mpf.gcode.', 'mpf.geometry.', 'mpf.storage.')):
                continue
            for target, line in edges:
                with self.subTest(module=module, target=target, line=line):
                    if target.startswith('mpf.'):
                        self.assertTrue(target.startswith(('mpf.gcode', 'mpf.geometry', 'mpf.storage')),
                                        'Core components must depend on core values or explicit injected capabilities')
                    else:
                        self.assertNotIn(target.split('.')[0], {'UM', 'cura', 'PyQt6'})

    def test_production_import_graph_is_acyclic_including_function_local_imports(self):
        graph, _imports = production_graph()
        complete = set()
        def visit(module, ancestors):
            if module in ancestors:
                self.fail('Production import cycle: '+ ' -> '.join((*ancestors, module)))
            if module in complete: return
            for dependency in sorted(graph[module]): visit(dependency, (*ancestors, module))
            complete.add(module)
        for module in sorted(graph): visit(module, ())

    def test_host_indexing_modules_have_one_application_owner_and_no_retired_paths(self):
        for name in ('GCodeIndexService', 'IndexTasks', 'PreparedSession', 'CacheNamespaces'):
            self.assertTrue((ROOT / 'mpf/application/indexing' / (name+'.py')).is_file())
            self.assertFalse((ROOT / 'mpf/gcode' / (name+'.py')).exists())
