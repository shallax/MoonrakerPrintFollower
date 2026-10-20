# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Share conservative process ownership checks across cache and transfer cleanup.

"""A read-only liveness verdict; uncertainty always preserves another owner."""
import os
import sys

def windows_process_alive(pid: int) -> bool:
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



def process_alive(pid: int) -> bool:
    if pid <= 0 or pid > 0xFFFFFFFF:
        return False
    if sys.platform == "win32":
        return windows_process_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    except OSError:
        return True
