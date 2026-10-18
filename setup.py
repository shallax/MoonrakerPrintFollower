# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Map shared sources into a separate consumer package without aliases.

"""Build metadata is in pyproject.toml; source ownership is in one manifest."""
from pathlib import Path
import sys
from setuptools import setup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.library_build import LibraryBuild
from tools.library_manifest import MODULES, PACKAGE_DIRS, RESOURCES

setup(packages=list(MODULES), package_dir=PACKAGE_DIRS,
      package_data=RESOURCES, cmdclass={"build_py": LibraryBuild})
