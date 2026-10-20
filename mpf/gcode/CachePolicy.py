# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-20 — 6.0.0: Use the shared conservative process-owner liveness probe.
# 2026-10-01 [1866243] Persist print sources and publish live download progress
# 2026-09-27 [bed0492] Harden RC networking, cache lifecycle, motion coordinates and UI diagnostics
# 2026-09-22 [4db7d92] 4.6.0: the cleanup pass — Windows liveness, true-LRU eviction, the structural work bound,
#   and the per-machine cache size

"""Shared print-folder LRU for raw sources, indexes and prepared layers."""
from __future__ import annotations

import os
import shutil
from typing import Dict, Optional, Tuple
from ..storage.ProcessOwner import process_alive


def active_cache_writer(directory: str) -> bool:
    return any((".mpfi.gz.tmp-" in name or ".mpfp.tmp-" in name
                or ".gcode.tmp-" in name)
               and temporary_owner_alive(name) for name in os.listdir(directory))


def evict_to_budget(totals: Dict[str, Tuple[float, int]],
                    max_bytes: int,
                    max_entries: Optional[int],
                    keep_dir: Optional[str]) -> Tuple[int, int]:
    """Evict the least recently used UNPROTECTED print folders until
    the retained set fits both budgets — oldest first, and every
    unprotected entry stays eligible for eviction while a budget is
    exceeded. The protected folder always survives; if it alone
    exceeds a budget, that over-budget state is the only acceptable
    one. `totals` maps each print folder to (recency, size) — the
    folder's latest access stamp. Returns the retained
    (bytes, entries)."""
    retained = sum(size for _recency, size in totals.values())
    entries = len(totals)
    for root, (_recency, size) in sorted(totals.items(),
                                         key=lambda item: item[1][0]):
        if root == keep_dir:
            continue  # protected: never a candidate
        if retained <= max_bytes and (max_entries is None
                                      or entries <= max_entries):
            break  # the retained set fits — stop evicting
        try:
            if active_cache_writer(root):
                continue
            shutil.rmtree(root, ignore_errors=True)
        except OSError:
            continue
        if not os.path.exists(root):
            retained -= size
            entries -= 1
    return retained, entries


def temporary_owner_alive(name: str) -> bool:
    """Keep temporary files unless their writer is provably gone."""
    try:
        pid = int(name.split(".tmp-", 1)[1].split("-", 1)[0])
    except (IndexError, ValueError):
        return False  # no live writer ever stamped this name
    return process_alive(pid)



def sweep_index_temps(directory: str) -> None:
    """Remove abandoned index writes; prepared temporaries are resumable."""
    for root, _dirs, names in os.walk(directory):
        for name in names:
            if ".mpfi.gz.tmp-" not in name or temporary_owner_alive(name):
                continue
            try:
                os.unlink(os.path.join(root, name))
            except OSError:
                pass
