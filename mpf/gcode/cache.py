# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Export cache capabilities without shadowing parser-package modules.

"""Portable cache and residency API, separate from parser implementation modules."""
from .IndexResidency import IndexResidency
from .SourceIdentity import CacheIdentity, SourceIdentity
from .IndexCache import PersistentIndexCache
from .PreparedStore import PreparedCache
from .RawSourceCache import RawSourceCache

__all__ = ['IndexResidency', 'CacheIdentity', 'SourceIdentity', 'PersistentIndexCache',
           'PreparedCache', 'RawSourceCache']
