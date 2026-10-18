# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Enforce the complete reusable dependency closure, including lazy imports.

"""The standalone wheel must not grow application or host dependencies."""
import ast
from importlib.util import resolve_name
from pathlib import Path
import sys
import unittest
from tools.library_manifest import MODULES, PACKAGE_DIRS, RESOURCES, source_files

ROOT = Path(__file__).resolve().parents[1]


class LibraryBoundaryTests(unittest.TestCase):
    def test_all_relative_dependencies_including_lazy_imports_are_shipped(self):
        sources = source_files()
        shipped = {name.removesuffix('.py').replace('/', '.') for name in sources if name.endswith('.py')}
        shipped |= set(MODULES)
        for package, modules in MODULES.items():
            for module in modules:
                path = ROOT / PACKAGE_DIRS[package] / (module + '.py')
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.ImportFrom) and node.level:
                        target = resolve_name('.' * node.level + (node.module or ''), package)
                        self.assertIn(target, shipped, (path, node.lineno, target))
                        if not node.module:
                            for alias in node.names:
                                self.assertIn(target + '.' + alias.name, shipped, (path, node.lineno))
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        names = [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
                        for name in names:
                            root = name.split('.')[0]
                            self.assertIn(root, sys.stdlib_module_names | {'PyQt6', 'numpy'}, (path, name))
                            self.assertNotIn(root, {'socket', 'urllib', 'http'}, path)

    def test_distribution_has_one_source_per_member_and_only_renderer_assets(self):
        sources = source_files()
        self.assertEqual(len(sources), len(set(sources.values())))
        self.assertEqual(set(MODULES), set(PACKAGE_DIRS))
        for path in sources.values():
            self.assertTrue((ROOT / path).is_file(), path)
        self.assertEqual(set(RESOURCES), {'moonraker_toolpath.resources'})
        self.assertTrue(all(name.startswith('shaders/stroke.') for name in RESOURCES['moonraker_toolpath.resources']))
        for forbidden in ('GCodeIndexService', 'MoonrakerClient', 'PrinterConfig', 'MoonrakerMonitorModel'):
            self.assertFalse(any(forbidden in name for name in sources), forbidden)
