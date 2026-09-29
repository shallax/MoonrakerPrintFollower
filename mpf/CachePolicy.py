"""The shared print-folder eviction policy (the review's unified-
eviction finding): the index cache and the prepared store evict
whole print folders by the SAME rule — true LRU, never a
recency-biased size packing."""
from __future__ import annotations

import os
import shutil
import sys
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
            if any((".mpfi.gz.tmp-" in name or ".mpfp.tmp-" in name)
                   and temporary_owner_alive(name) for name in os.listdir(root)):
                continue
            shutil.rmtree(root, ignore_errors=True)
        except OSError:
            continue
        if not os.path.exists(root):
            retained -= size
            entries -= 1
    return retained, entries


def _windows_liveness(pid: int) -> bool:
    """The Windows owner-liveness verdict through the native process
    API, dependency-free: a query-limited handle opens only while
    the process OBJECT exists, and its exit code leaves
    STILL_ACTIVE once the process is gone — so a child that exited
    and was waited reads dead even while its zombie object lingers
    in the waiter. ERROR_INVALID_PARAMETER names no live process
    (dead); every other failure is indeterminate and keeps the tmp
    (conservative)."""
    import ctypes
    from ctypes import wintypes
    # Explicit Win32 signatures (the review's 64-bit hardening): a
    # HANDLE is pointer-sized, so the default c_int restype would
    # truncate it; use_last_error makes the failure verdict read
    # from ctypes.get_last_error() coherently.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _OpenProcess = kernel32.OpenProcess
    _OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _OpenProcess.restype = wintypes.HANDLE
    _GetExitCodeProcess = kernel32.GetExitCodeProcess
    _GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _GetExitCodeProcess.restype = wintypes.BOOL
    _CloseHandle = kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    handle = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87  # 87: no such process
    try:
        code = wintypes.DWORD()
        if not _GetExitCodeProcess(handle, ctypes.byref(code)):
            return True  # indeterminate — keep the tmp
        return code.value == _STILL_ACTIVE
    finally:
        _CloseHandle(handle)

def temporary_owner_alive(name: str) -> bool:
    """Keep temporary files unless their writer is provably gone."""
    try:
        pid = int(name.split(".tmp-", 1)[1].split("-", 1)[0])
    except (IndexError, ValueError):
        return False  # no live writer ever stamped this name
    if pid <= 0:
        return False  # impossible: writers stamp their real pid
    if pid > 0xFFFFFFFF:
        return False  # beyond any platform's pid space
    if sys.platform == "win32":
        return _windows_liveness(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False  # no such process
    except PermissionError:
        return True  # cannot disprove the owner — keep the tmp
    except OverflowError:
        return False  # beyond the platform's pid space
    except OSError:
        return True  # indeterminate — keep the tmp



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
