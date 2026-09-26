"""The shared print-folder eviction policy (the review's unified-
eviction finding): the index cache and the prepared store evict
whole print folders by the SAME rule — true LRU, never a
recency-biased size packing."""
from __future__ import annotations

import os
import shutil
from typing import Dict, Optional, Tuple


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
            shutil.rmtree(root, ignore_errors=True)
        except OSError:
            continue
        if not os.path.exists(root):
            retained -= size
            entries -= 1
    return retained, entries
