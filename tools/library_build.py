# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Build only shared library owners, excluding application services.

"""Setuptools hook: reuse source files without copying implementations or aliases."""
import shutil
from setuptools.command.build_py import build_py
from tools.library_manifest import MODULES


class LibraryBuild(build_py):
    def run(self):
        # Removed modules must not leak out of a previous local wheel build.
        shutil.rmtree(self.build_lib, ignore_errors=True)
        super().run()

    def find_package_modules(self, package, package_dir):
        return [entry for entry in super().find_package_modules(package, package_dir)
                if entry[1] in MODULES[package]]
