# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Accept caller-owned stable source identities without a printer protocol dependency.

"""Neutral cache identity contract; callers decide how their source is identified."""
from dataclasses import dataclass
from typing import Protocol


class SourceIdentity(Protocol):
    filename: str
    size: int
    modified: float
    uuid: str

    def stable_key(self) -> str: ...


@dataclass(frozen=True)
class CacheIdentity:
    """A caller-supplied key and optional metadata; never guesses content identity."""
    key: str
    filename: str = ''
    size: int = 0
    modified: float = 0.0
    uuid: str = ''

    def stable_key(self) -> str:
        return self.key
