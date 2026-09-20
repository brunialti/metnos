# SPDX-License-Identifier: AGPL-3.0-only
"""Windows process-containment proof for the RM-0008 birth runner.

The primitive in this module proves only process-tree containment and resource
limits.  It intentionally does *not* claim filesystem, network, token, or
credential isolation.  Consequently the productive birth runner remains
``test_environment_unavailable`` on Windows until a separately certified
AppContainer or restricted-token boundary is available.

The implementation uses only Win32 facilities present on Windows Server 2022
(the GitHub-hosted ``windows-2022`` image): a process is created suspended,
assigned to a Job Object, and resumed only after assignment succeeds.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


WINDOWS_JOB_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
WINDOWS_JOB_MAX_PROCESSES = 32
WINDOWS_JOB_TIMEOUT_S = 10.0
WINDOWS_JOB_DRAIN_S = 2.0

WINDOWS_PROBE_ENV: Mapping[str, str] = {
    "HOME": r"C:\work",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": r"C:\Windows\System32",
    "TEMP": r"C:\work\tmp",
    "TMP": r"C:\work\tmp",
    "TZ": "UTC",
    "USERPROFILE": r"C:\work",
}


@dataclass(frozen=True, slots=True)
class WindowsJobAttestation:
    available: bool
    error_code: str | None
    assigned_before_resume: bool
    kill_on_job_close: bool
    memory_limit_bytes: int
    max_processes: int
    active_processes: int | None
    tree_empty: bool
    host_isolation_attested: bool


class WindowsJobError(RuntimeError):
    pass


def _unavailable(code: str) -> WindowsJobAttestation:
    return WindowsJobAttestation(
        available=False,
        error_code=code,
        assigned_before_resume=False,
        kill_on_job_close=False,
        memory_limit_bytes=WINDOWS_JOB_MEMORY_LIMIT_BYTES,
        max_processes=WINDOWS_JOB_MAX_PROCESSES,
        active_processes=None,
        tree_empty=False,
        host_isolation_attested=False,
    )


def _closed_command(command: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
        raise ValueError("command_invalid")
    result = tuple(command)
    if not result or any(
        not isinstance(part, str) or not part or "\x00" in part
        for part in result
    ):
        raise ValueError("command_invalid")
    return result


def _environment_block(environment: Mapping[str, str]) -> str:
    entries: list[str] = []
    for key in sorted(environment, key=str.upper):
        value = environment[key]
        if (
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\x00" in key
            or not isinstance(value, str)
            or "\x00" in value
        ):
            raise ValueError("environment_invalid")
        entries.append(f"{key}={value}")
    return "\x00".join(entries) + "\x00\x00"


def _run_job_object_probe(
    command: Sequence[str],
    *,
    timeout_s: float = WINDOWS_JOB_TIMEOUT_S,
) -> WindowsJobAttestation:
    """Run a trusted certification probe inside an attested Job Object.

    This private function is deliberately not a candidate executor backend.
    The child still has the caller's Windows token and host visibility.
    """
    argv = _closed_command(command)
    if os.name != "nt":
        return _unavailable("windows_job_object_platform_unavailable")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError("timeout_invalid")

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
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

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    CREATE_SUSPENDED = 0x00000004
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    CREATE_NO_WINDOW = 0x08000000
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectBasicAccountingInformation = 1
    JobObjectExtendedLimitInformation = 9

    for fn in (
        "CreateJobObjectW", "SetInformationJobObject", "CreateProcessW",
        "AssignProcessToJobObject", "ResumeThread", "WaitForSingleObject",
        "TerminateJobObject", "QueryInformationJobObject", "CloseHandle",
    ):
        if not hasattr(kernel32, fn):
            return _unavailable("windows_job_api_unavailable")

    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION),
    )
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return _unavailable("windows_job_create_failed")
    process_info = PROCESS_INFORMATION()
    assigned = False
    try:
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        limits.BasicLimitInformation.ActiveProcessLimit = WINDOWS_JOB_MAX_PROCESSES
        limits.ProcessMemoryLimit = WINDOWS_JOB_MEMORY_LIMIT_BYTES
        limits.JobMemoryLimit = WINDOWS_JOB_MEMORY_LIMIT_BYTES
        if not kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            return _unavailable("windows_job_limits_failed")

        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        environment = ctypes.create_unicode_buffer(_environment_block(WINDOWS_PROBE_ENV))
        with tempfile.TemporaryDirectory(prefix="metnos-birth-win-job-") as temp:
            work = Path(temp)
            (work / "tmp").mkdir()
            if not kernel32.CreateProcessW(
                None,
                command_line,
                None,
                None,
                False,
                CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                environment,
                str(work),
                ctypes.byref(startup),
                ctypes.byref(process_info),
            ):
                return _unavailable("windows_process_create_failed")
            try:
                if not kernel32.AssignProcessToJobObject(job, process_info.hProcess):
                    kernel32.TerminateJobObject(job, 125)
                    return _unavailable("windows_job_assignment_failed")
                assigned = True
                if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
                    kernel32.TerminateJobObject(job, 125)
                    return _unavailable("windows_process_resume_failed")
                wait_ms = max(1, int(float(timeout_s) * 1000))
                wait = kernel32.WaitForSingleObject(process_info.hProcess, wait_ms)
                if wait == WAIT_TIMEOUT:
                    kernel32.TerminateJobObject(job, 124)
                elif wait != WAIT_OBJECT_0:
                    kernel32.TerminateJobObject(job, 125)
                else:
                    # A successful root process may still have left children.
                    # End the entire job before proving ActiveProcesses == 0.
                    kernel32.TerminateJobObject(job, 0)
            finally:
                if process_info.hThread:
                    kernel32.CloseHandle(process_info.hThread)
                if process_info.hProcess:
                    kernel32.CloseHandle(process_info.hProcess)

        deadline = time.monotonic() + WINDOWS_JOB_DRAIN_S
        active: int | None = None
        while time.monotonic() <= deadline:
            accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
            if not kernel32.QueryInformationJobObject(
                job,
                JobObjectBasicAccountingInformation,
                ctypes.byref(accounting),
                ctypes.sizeof(accounting),
                None,
            ):
                return _unavailable("windows_job_query_failed")
            active = int(accounting.ActiveProcesses)
            if active == 0:
                break
            time.sleep(0.01)
        empty = active == 0
        return WindowsJobAttestation(
            available=True,
            error_code=None if empty else "windows_job_tree_not_empty",
            assigned_before_resume=assigned,
            kill_on_job_close=True,
            memory_limit_bytes=WINDOWS_JOB_MEMORY_LIMIT_BYTES,
            max_processes=WINDOWS_JOB_MAX_PROCESSES,
            active_processes=active,
            tree_empty=empty,
            host_isolation_attested=False,
        )
    finally:
        # KILL_ON_JOB_CLOSE is the final containment valve even after an error.
        kernel32.CloseHandle(job)


__all__ = [
    "WINDOWS_JOB_DRAIN_S",
    "WINDOWS_JOB_MAX_PROCESSES",
    "WINDOWS_JOB_MEMORY_LIMIT_BYTES",
    "WINDOWS_JOB_TIMEOUT_S",
    "WINDOWS_PROBE_ENV",
    "WindowsJobAttestation",
]
