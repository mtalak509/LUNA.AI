"""Windows process-tree ownership; importing this module is safe on POSIX."""

import ctypes
import os
from ctypes import wintypes
from typing import Any, NoReturn, cast

from core.agent.shell.models import ShellError

JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100

# ctypes exposes this platform-specific API dynamically; keep Any at the FFI boundary.
_windows_ctypes: Any = ctypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _load_api() -> Any:
    # These ctypes entry points exist only on Windows. Resolve them after the OS guard
    # so this module remains importable and type-checkable on Linux as well.
    api = _windows_ctypes.WinDLL("kernel32", use_last_error=True)

    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    api.SetInformationJobObject.restype = wintypes.BOOL
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.AssignProcessToJobObject.restype = wintypes.BOOL
    api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateJobObject.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _last_error() -> OSError:
    code = _windows_ctypes.get_last_error()
    return cast(OSError, _windows_ctypes.WinError(code))


class WindowsJob:
    """Own a Windows process tree through a kill-on-close Job Object."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ShellError("Windows Job Objects require Windows")

        self._api = _load_api()
        self._handle: int | None = self._api.CreateJobObjectW(None, None)
        if not self._handle:
            self._handle = None
            self._raise_error("CreateJobObjectW")

        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._api.SetInformationJobObject(
            self._handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = _last_error()
            self.close()
            raise ShellError(f"Cannot configure Windows job: {error}") from error

    @staticmethod
    def _raise_error(operation: str) -> NoReturn:
        error = _last_error()
        raise ShellError(f"{operation} failed: {error}") from error

    def _require_handle(self) -> int:
        if self._handle is None:
            raise ShellError("Windows job is already closed")
        return self._handle

    def assign(self, pid: int) -> None:
        handle = self._require_handle()
        process = self._api.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not process:
            self._raise_error("OpenProcess")
        try:
            if not self._api.AssignProcessToJobObject(handle, process):
                self._raise_error("AssignProcessToJobObject")
        finally:
            self._api.CloseHandle(process)

    def terminate(self) -> None:
        if self._handle is None:
            return
        if not self._api.TerminateJobObject(self._handle, 1):
            self._raise_error("TerminateJobObject")

    def close(self) -> None:
        if self._handle is None:
            return
        if not self._api.CloseHandle(self._handle):
            self._raise_error("CloseHandle")
        self._handle = None
