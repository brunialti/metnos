"""Independent Win32 identity and ACL oracle for the RM-0008 CI barrier.

This module must not import Metnos runtime code.  The controller runs with the
administrative runner token; child probes run under freshly-created standard
local accounts and exercise access with CreateFileW rather than AccessCheck.
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import secrets
import string
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal


if os.name != "nt":  # The dedicated workflow job must never turn this into a skip.
    raise RuntimeError("the real-identity oracle requires Windows")


from ctypes import wintypes


_ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_NETAPI32 = ctypes.WinDLL("netapi32", use_last_error=True)

_ERROR_ACCESS_DENIED = 5
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_MEMBER_IN_ALIAS = 1378
_ERROR_NOT_ALL_ASSIGNED = 1300
_NERR_SUCCESS = 0
_NERR_USER_NOT_FOUND = 2221

_ACCESS_ALLOWED_ACE_TYPE = 0
_ACL_SIZE_INFORMATION_CLASS = 2
_DACL_SECURITY_INFORMATION = 0x00000004
_OWNER_SECURITY_INFORMATION = 0x00000001
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000
_SE_FILE_OBJECT = 1

_CREATE_NEW = 1
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_PERSISTENT_ACLS = 0x00000008
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_APPEND_DATA = 0x00000004
_FILE_DELETE_CHILD = 0x00000040
_DELETE = 0x00010000
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_FILE_ALL_ACCESS = 0x001F01FF
_FILE_READ_MASK = 0x00120089
_DIRECTORY_READ_MASK = 0x001200A9

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_STILL_ACTIVE = 259

_TOKEN_DUPLICATE = 0x0002
_TOKEN_QUERY = 0x0008
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_SE_PRIVILEGE_ENABLED = 0x00000002
_TOKEN_USER_CLASS = 1
_TOKEN_ELEVATION_TYPE_CLASS = 18
_TOKEN_ELEVATION_CLASS = 20
_TOKEN_INTEGRITY_LEVEL_CLASS = 25
_TOKEN_ELEVATION_TYPE_DEFAULT = 1
_SECURITY_IDENTIFICATION = 1
_SECURITY_MANDATORY_HIGH_RID = 0x3000

_USER_PRIV_USER = 1
_UF_SCRIPT = 0x0001
_UF_NORMAL_ACCOUNT = 0x0200
_UF_DONT_EXPIRE_PASSWD = 0x10000
_LG_INCLUDE_INDIRECT = 0x0001
_MAX_PREFERRED_LENGTH = 0xFFFFFFFF
_SID_TYPE_USER = 1
_WIN_BUILTIN_ADMINISTRATORS_SID = 26
_WIN_BUILTIN_USERS_SID = 27
_SECURITY_MAX_SID_SIZE = 68

_SDDL_REVISION_1 = 1
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

ACCESS_RESULT_ALLOWED = 0
ACCESS_RESULT_DENIED = 10
ACCESS_RESULT_UNEXPECTED = 20


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", _LUID_AND_ATTRIBUTES * 1),
    ]


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


class _TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


class _STARTUPINFOW(ctypes.Structure):
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


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _WIN32_FIND_DATAW(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("dwReserved0", wintypes.DWORD),
        ("dwReserved1", wintypes.DWORD),
        ("cFileName", wintypes.WCHAR * 260),
        ("cAlternateFileName", wintypes.WCHAR * 14),
    ]


class _USER_INFO_1(ctypes.Structure):
    _fields_ = [
        ("usri1_name", wintypes.LPWSTR),
        ("usri1_password", wintypes.LPWSTR),
        ("usri1_password_age", wintypes.DWORD),
        ("usri1_priv", wintypes.DWORD),
        ("usri1_home_dir", wintypes.LPWSTR),
        ("usri1_comment", wintypes.LPWSTR),
        ("usri1_flags", wintypes.DWORD),
        ("usri1_script_path", wintypes.LPWSTR),
    ]


class _LOCALGROUP_MEMBERS_INFO_0(ctypes.Structure):
    _fields_ = [("lgrmi0_sid", ctypes.c_void_p)]


class _LOCALGROUP_USERS_INFO_0(ctypes.Structure):
    _fields_ = [("lgrui0_name", wintypes.LPWSTR)]


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class _ACE_HEADER(ctypes.Structure):
    _fields_ = [
        ("AceType", wintypes.BYTE),
        ("AceFlags", wintypes.BYTE),
        ("AceSize", wintypes.WORD),
    ]


class _ACCESS_ALLOWED_ACE(ctypes.Structure):
    _fields_ = [
        ("Header", _ACE_HEADER),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


_KERNEL32.GetCurrentProcess.restype = wintypes.HANDLE
_KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
_KERNEL32.CloseHandle.restype = wintypes.BOOL
_KERNEL32.LocalFree.argtypes = (ctypes.c_void_p,)
_KERNEL32.LocalFree.restype = ctypes.c_void_p
_KERNEL32.CreateFileW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
)
_KERNEL32.CreateFileW.restype = wintypes.HANDLE
_KERNEL32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_KERNEL32.WaitForSingleObject.restype = wintypes.DWORD
_KERNEL32.GetExitCodeProcess.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
)
_KERNEL32.GetExitCodeProcess.restype = wintypes.BOOL
_KERNEL32.ReadFile.argtypes = (
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
)
_KERNEL32.ReadFile.restype = wintypes.BOOL
_KERNEL32.FindFirstFileW.argtypes = (
    wintypes.LPCWSTR,
    ctypes.POINTER(_WIN32_FIND_DATAW),
)
_KERNEL32.FindFirstFileW.restype = wintypes.HANDLE
_KERNEL32.FindClose.argtypes = (wintypes.HANDLE,)
_KERNEL32.FindClose.restype = wintypes.BOOL
_KERNEL32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_KERNEL32.TerminateProcess.restype = wintypes.BOOL
_KERNEL32.ResumeThread.argtypes = (wintypes.HANDLE,)
_KERNEL32.ResumeThread.restype = wintypes.DWORD
_KERNEL32.GetVolumeInformationW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    wintypes.DWORD,
)
_KERNEL32.GetVolumeInformationW.restype = wintypes.BOOL

_ADVAPI32.OpenProcessToken.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
)
_ADVAPI32.OpenProcessToken.restype = wintypes.BOOL
_ADVAPI32.GetTokenInformation.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
_ADVAPI32.GetTokenInformation.restype = wintypes.BOOL
_ADVAPI32.DuplicateToken.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.POINTER(wintypes.HANDLE),
)
_ADVAPI32.DuplicateToken.restype = wintypes.BOOL
_ADVAPI32.CheckTokenMembership.argtypes = (
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.BOOL),
)
_ADVAPI32.CheckTokenMembership.restype = wintypes.BOOL
_ADVAPI32.LookupPrivilegeValueW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.POINTER(_LUID),
)
_ADVAPI32.LookupPrivilegeValueW.restype = wintypes.BOOL
_ADVAPI32.AdjustTokenPrivileges.argtypes = (
    wintypes.HANDLE,
    wintypes.BOOL,
    ctypes.POINTER(_TOKEN_PRIVILEGES),
    wintypes.DWORD,
    ctypes.POINTER(_TOKEN_PRIVILEGES),
    ctypes.POINTER(wintypes.DWORD),
)
_ADVAPI32.AdjustTokenPrivileges.restype = wintypes.BOOL
_ADVAPI32.LookupAccountNameW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
)
_ADVAPI32.LookupAccountNameW.restype = wintypes.BOOL
_ADVAPI32.LookupAccountSidW.argtypes = (
    wintypes.LPCWSTR,
    ctypes.c_void_p,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
)
_ADVAPI32.LookupAccountSidW.restype = wintypes.BOOL
_ADVAPI32.ConvertSidToStringSidW.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.LPWSTR),
)
_ADVAPI32.ConvertSidToStringSidW.restype = wintypes.BOOL
_ADVAPI32.ConvertStringSidToSidW.argtypes = (
    wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.c_void_p),
)
_ADVAPI32.ConvertStringSidToSidW.restype = wintypes.BOOL
_ADVAPI32.CreateWellKnownSid.argtypes = (
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
)
_ADVAPI32.CreateWellKnownSid.restype = wintypes.BOOL
_ADVAPI32.EqualSid.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
_ADVAPI32.EqualSid.restype = wintypes.BOOL
_ADVAPI32.IsValidSid.argtypes = (ctypes.c_void_p,)
_ADVAPI32.IsValidSid.restype = wintypes.BOOL
_ADVAPI32.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
_ADVAPI32.GetSidSubAuthorityCount.restype = ctypes.POINTER(wintypes.BYTE)
_ADVAPI32.GetSidSubAuthority.argtypes = (ctypes.c_void_p, wintypes.DWORD)
_ADVAPI32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
_ADVAPI32.CreateProcessWithLogonW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(_STARTUPINFOW),
    ctypes.POINTER(_PROCESS_INFORMATION),
)
_ADVAPI32.CreateProcessWithLogonW.restype = wintypes.BOOL
_ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.DWORD),
)
_ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
_ADVAPI32.GetSecurityDescriptorOwner.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.BOOL),
)
_ADVAPI32.GetSecurityDescriptorOwner.restype = wintypes.BOOL
_ADVAPI32.GetSecurityDescriptorDacl.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.BOOL),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.BOOL),
)
_ADVAPI32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
_ADVAPI32.SetSecurityInfo.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)
_ADVAPI32.SetSecurityInfo.restype = wintypes.DWORD
_ADVAPI32.GetSecurityInfo.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
)
_ADVAPI32.GetSecurityInfo.restype = wintypes.DWORD
_ADVAPI32.GetSecurityDescriptorControl.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.WORD),
    ctypes.POINTER(wintypes.DWORD),
)
_ADVAPI32.GetSecurityDescriptorControl.restype = wintypes.BOOL
_ADVAPI32.GetAclInformation.argtypes = (
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_int,
)
_ADVAPI32.GetAclInformation.restype = wintypes.BOOL
_ADVAPI32.GetAce.argtypes = (
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
)
_ADVAPI32.GetAce.restype = wintypes.BOOL

_NETAPI32.NetUserAdd.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
)
_NETAPI32.NetUserAdd.restype = wintypes.DWORD
_NETAPI32.NetUserDel.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
_NETAPI32.NetUserDel.restype = wintypes.DWORD
_NETAPI32.NetLocalGroupAddMembers.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
)
_NETAPI32.NetLocalGroupAddMembers.restype = wintypes.DWORD
_NETAPI32.NetUserGetLocalGroups.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
)
_NETAPI32.NetUserGetLocalGroups.restype = wintypes.DWORD
_NETAPI32.NetApiBufferFree.argtypes = (ctypes.c_void_p,)
_NETAPI32.NetApiBufferFree.restype = wintypes.DWORD


def _raise_last_error(operation: str) -> None:
    raise ctypes.WinError(ctypes.get_last_error(), operation)


def _close_handle(handle: int | None) -> None:
    if handle and handle != _INVALID_HANDLE_VALUE:
        if not _KERNEL32.CloseHandle(handle):
            _raise_last_error("CloseHandle")


def _open_path(path: Path, access: int, *, directory: bool) -> int:
    flags = _FILE_FLAG_BACKUP_SEMANTICS if directory else _FILE_ATTRIBUTE_NORMAL
    handle = _KERNEL32.CreateFileW(
        str(path),
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        _raise_last_error("CreateFileW")
    return handle


def _sid_to_string(sid: int) -> str:
    text = wintypes.LPWSTR()
    if not _ADVAPI32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        _raise_last_error("ConvertSidToStringSidW")
    try:
        return text.value
    finally:
        _KERNEL32.LocalFree(text)


@contextlib.contextmanager
def _string_sid(value: str) -> Iterator[int]:
    sid = ctypes.c_void_p()
    if not _ADVAPI32.ConvertStringSidToSidW(value, ctypes.byref(sid)):
        _raise_last_error("ConvertStringSidToSidW")
    try:
        yield sid.value
    finally:
        _KERNEL32.LocalFree(sid)


def _well_known_sid(kind: int) -> ctypes.Array[ctypes.c_char]:
    size = wintypes.DWORD(_SECURITY_MAX_SID_SIZE)
    buffer = ctypes.create_string_buffer(size.value)
    if not _ADVAPI32.CreateWellKnownSid(kind, None, buffer, ctypes.byref(size)):
        _raise_last_error("CreateWellKnownSid")
    return buffer


def _lookup_sid_name(sid: int) -> str:
    name_size = wintypes.DWORD()
    domain_size = wintypes.DWORD()
    usage = wintypes.DWORD()
    ctypes.set_last_error(0)
    _ADVAPI32.LookupAccountSidW(
        None,
        sid,
        None,
        ctypes.byref(name_size),
        None,
        ctypes.byref(domain_size),
        ctypes.byref(usage),
    )
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        _raise_last_error("LookupAccountSidW(size)")
    name = ctypes.create_unicode_buffer(name_size.value)
    domain = ctypes.create_unicode_buffer(domain_size.value)
    if not _ADVAPI32.LookupAccountSidW(
        None,
        sid,
        name,
        ctypes.byref(name_size),
        domain,
        ctypes.byref(domain_size),
        ctypes.byref(usage),
    ):
        _raise_last_error("LookupAccountSidW")
    return name.value


def _lookup_account_sid(name: str) -> tuple[ctypes.Array[ctypes.c_char], str]:
    sid_size = wintypes.DWORD()
    domain_size = wintypes.DWORD()
    usage = wintypes.DWORD()
    ctypes.set_last_error(0)
    _ADVAPI32.LookupAccountNameW(
        None,
        name,
        None,
        ctypes.byref(sid_size),
        None,
        ctypes.byref(domain_size),
        ctypes.byref(usage),
    )
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        _raise_last_error("LookupAccountNameW(size)")
    sid = ctypes.create_string_buffer(sid_size.value)
    domain = ctypes.create_unicode_buffer(domain_size.value)
    if not _ADVAPI32.LookupAccountNameW(
        None,
        name,
        sid,
        ctypes.byref(sid_size),
        domain,
        ctypes.byref(domain_size),
        ctypes.byref(usage),
    ):
        _raise_last_error("LookupAccountNameW")
    if usage.value != _SID_TYPE_USER:
        raise AssertionError("temporary account did not resolve to SidTypeUser")
    return sid, _sid_to_string(ctypes.addressof(sid))


def _local_groups(username: str) -> set[str]:
    buffer = ctypes.c_void_p()
    entries = wintypes.DWORD()
    total = wintypes.DWORD()
    result = _NETAPI32.NetUserGetLocalGroups(
        None,
        username,
        0,
        _LG_INCLUDE_INDIRECT,
        ctypes.byref(buffer),
        _MAX_PREFERRED_LENGTH,
        ctypes.byref(entries),
        ctypes.byref(total),
    )
    if result != _NERR_SUCCESS:
        raise OSError(result, "NetUserGetLocalGroups")
    try:
        if not buffer.value:
            return set()
        values = ctypes.cast(
            buffer,
            ctypes.POINTER(_LOCALGROUP_USERS_INFO_0 * entries.value),
        ).contents
        return {values[index].lgrui0_name for index in range(entries.value)}
    finally:
        if buffer.value:
            free_result = _NETAPI32.NetApiBufferFree(buffer)
            if free_result != _NERR_SUCCESS:
                raise OSError(free_result, "NetApiBufferFree")


@dataclass(slots=True)
class LocalAccount:
    username: str
    sid: str
    password: ctypes.Array[ctypes.c_wchar] = field(repr=False)

    def erase_password(self) -> None:
        ctypes.memset(
            ctypes.addressof(self.password), 0, ctypes.sizeof(self.password)
        )


def create_standard_account(prefix: str) -> LocalAccount:
    suffix = secrets.token_hex(5)
    username = f"{prefix}{suffix}"[:20]
    alphabet = string.ascii_letters + string.digits + "!@#%_-"
    password = ctypes.create_unicode_buffer(33)
    for index, character in enumerate("Rm8!"):
        password[index] = character
    for index in range(4, 32):
        password[index] = secrets.choice(alphabet)
    information = _USER_INFO_1(
        username,
        ctypes.cast(password, wintypes.LPWSTR),
        0,
        _USER_PRIV_USER,
        None,
        "Metnos RM-0008 ephemeral CI identity",
        _UF_SCRIPT | _UF_NORMAL_ACCOUNT | _UF_DONT_EXPIRE_PASSWD,
        None,
    )
    parameter_error = wintypes.DWORD()
    result = _NETAPI32.NetUserAdd(
        None, 1, ctypes.byref(information), ctypes.byref(parameter_error)
    )
    if result != _NERR_SUCCESS:
        ctypes.memset(ctypes.addressof(password), 0, ctypes.sizeof(password))
        raise OSError(result, f"NetUserAdd parameter {parameter_error.value}")
    try:
        sid_buffer, sid_string = _lookup_account_sid(username)
        users_sid = _well_known_sid(_WIN_BUILTIN_USERS_SID)
        users_name = _lookup_sid_name(ctypes.addressof(users_sid))
        member = _LOCALGROUP_MEMBERS_INFO_0(ctypes.addressof(sid_buffer))
        group_result = _NETAPI32.NetLocalGroupAddMembers(
            None, users_name, 0, ctypes.byref(member), 1
        )
        if group_result not in {_NERR_SUCCESS, _ERROR_MEMBER_IN_ALIAS}:
            raise OSError(group_result, "NetLocalGroupAddMembers")
        groups = {value.casefold() for value in _local_groups(username)}
        if users_name.casefold() not in groups:
            raise AssertionError("temporary account is not a member of BUILTIN\\Users")
        return LocalAccount(username, sid_string, password)
    except BaseException as primary_error:
        rollback_errors: list[BaseException] = []
        try:
            delete_result = _NETAPI32.NetUserDel(None, username)
            if delete_result not in {_NERR_SUCCESS, _NERR_USER_NOT_FOUND}:
                rollback_errors.append(OSError(delete_result, "NetUserDel(rollback)"))
        finally:
            ctypes.memset(ctypes.addressof(password), 0, ctypes.sizeof(password))
        if rollback_errors:
            raise BaseExceptionGroup(
                "account setup and rollback both failed",
                [primary_error, *rollback_errors],
            )
        raise


def delete_account(account: LocalAccount) -> None:
    try:
        result = _NETAPI32.NetUserDel(None, account.username)
        if result not in {_NERR_SUCCESS, _NERR_USER_NOT_FOUND}:
            raise OSError(result, "NetUserDel")
    finally:
        account.erase_password()


def _token_information(token: int, information_class: int) -> ctypes.Array:
    size = wintypes.DWORD()
    ctypes.set_last_error(0)
    _ADVAPI32.GetTokenInformation(
        token, information_class, None, 0, ctypes.byref(size)
    )
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        _raise_last_error("GetTokenInformation(size)")
    buffer = ctypes.create_string_buffer(size.value)
    if not _ADVAPI32.GetTokenInformation(
        token, information_class, buffer, size, ctypes.byref(size)
    ):
        _raise_last_error("GetTokenInformation")
    return buffer


def _fixed_token_information(
    token: int,
    information_class: int,
    value_type: type[ctypes.Structure] | type[wintypes.DWORD],
) -> ctypes.Structure | wintypes.DWORD:
    value = value_type()
    returned = wintypes.DWORD()
    expected = ctypes.sizeof(value)
    if not _ADVAPI32.GetTokenInformation(
        token,
        information_class,
        ctypes.byref(value),
        expected,
        ctypes.byref(returned),
    ):
        _raise_last_error("GetTokenInformation(fixed)")
    if returned.value != expected:
        raise AssertionError("fixed token information returned an unexpected size")
    return value


@dataclass(frozen=True, slots=True)
class TokenFacts:
    user_sid: str
    elevated: bool
    elevation_type: int
    integrity_rid: int
    administrator: bool


def inspect_token(token: int) -> TokenFacts:
    user_buffer = _token_information(token, _TOKEN_USER_CLASS)
    user_sid = ctypes.cast(user_buffer, ctypes.POINTER(_TOKEN_USER)).contents.User.Sid
    user_sid_string = _sid_to_string(user_sid)

    elevation = _fixed_token_information(
        token, _TOKEN_ELEVATION_CLASS, _TOKEN_ELEVATION
    )
    if not isinstance(elevation, _TOKEN_ELEVATION):
        raise AssertionError("TokenElevation returned the wrong structure")
    elevated = bool(elevation.TokenIsElevated)
    elevation_type_value = _fixed_token_information(
        token, _TOKEN_ELEVATION_TYPE_CLASS, wintypes.DWORD
    )
    if not isinstance(elevation_type_value, wintypes.DWORD):
        raise AssertionError("TokenElevationType returned the wrong structure")
    elevation_type = elevation_type_value.value

    integrity_buffer = _token_information(token, _TOKEN_INTEGRITY_LEVEL_CLASS)
    integrity_sid = ctypes.cast(
        integrity_buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)
    ).contents.Label.Sid
    count_pointer = _ADVAPI32.GetSidSubAuthorityCount(integrity_sid)
    if not count_pointer or count_pointer.contents.value == 0:
        raise AssertionError("token has no integrity RID")
    integrity_rid = _ADVAPI32.GetSidSubAuthority(
        integrity_sid, count_pointer.contents.value - 1
    ).contents.value

    duplicate = wintypes.HANDLE()
    if not _ADVAPI32.DuplicateToken(
        token, _SECURITY_IDENTIFICATION, ctypes.byref(duplicate)
    ):
        _raise_last_error("DuplicateToken")
    try:
        administrators_sid = _well_known_sid(_WIN_BUILTIN_ADMINISTRATORS_SID)
        member = wintypes.BOOL()
        if not _ADVAPI32.CheckTokenMembership(
            duplicate, ctypes.addressof(administrators_sid), ctypes.byref(member)
        ):
            _raise_last_error("CheckTokenMembership")
        administrator = bool(member.value)
    finally:
        _close_handle(duplicate.value)

    return TokenFacts(
        user_sid_string,
        elevated,
        elevation_type,
        integrity_rid,
        administrator,
    )


def current_token_facts() -> TokenFacts:
    token = wintypes.HANDLE()
    if not _ADVAPI32.OpenProcessToken(
        _KERNEL32.GetCurrentProcess(),
        _TOKEN_QUERY | _TOKEN_DUPLICATE,
        ctypes.byref(token),
    ):
        _raise_last_error("OpenProcessToken")
    try:
        return inspect_token(token.value)
    finally:
        _close_handle(token.value)


def _assert_standard_token(token: int, expected_sid: str) -> None:
    facts = inspect_token(token)
    if facts.user_sid.casefold() != expected_sid.casefold():
        raise AssertionError("child token SID differs from the requested account")
    if facts.elevated:
        raise AssertionError("child token is elevated")
    if facts.administrator:
        raise AssertionError("child token has BUILTIN\\Administrators enabled")
    if facts.integrity_rid >= _SECURITY_MANDATORY_HIGH_RID:
        raise AssertionError("child token has high or system integrity")
    if facts.elevation_type != _TOKEN_ELEVATION_TYPE_DEFAULT:
        raise AssertionError("standard child token has an unexpected linked-token type")


def run_probe_as(
    account: LocalAccount,
    probe_script: Path,
    operation: str,
    target: Path,
    *,
    directory: bool = False,
    timeout_ms: int = 30_000,
) -> int:
    # Temporary diagnostic: the child of CreateProcessWithLogonW cannot inherit
    # handles, so its output would be lost.  It is redirected into a file the
    # caller can read back.
    diagnostic = target.parent / "probe-output.txt"
    child_argv = [
        str(probe_script),
        "--child",
        operation,
        str(target),
        "directory" if directory else "file",
    ]
    inline = (
        "import runpy,sys\n"
        f"sys.argv={child_argv!r}\n"
        f"handle=open({str(diagnostic)!r},'w',encoding='utf-8')\n"
        "sys.stdout=handle\nsys.stderr=handle\n"
        "try:\n"
        f"    runpy.run_path({str(probe_script)!r}, run_name='__main__')\n"
        "except SystemExit as exit_code:\n"
        "    handle.flush()\n"
        "    raise\n"
        "except BaseException:\n"
        "    import traceback\n"
        "    traceback.print_exc(file=handle)\n"
        "    handle.flush()\n"
        "    raise\n"
        "finally:\n"
        "    handle.flush()\n"
    )
    arguments = [sys.executable, "-I", "-B", "-c", inline]
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
    startup = _STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    process = _PROCESS_INFORMATION()
    if not _ADVAPI32.CreateProcessWithLogonW(
        account.username,
        ".",
        ctypes.cast(account.password, wintypes.LPCWSTR),
        0,
        sys.executable,
        command_line,
        _CREATE_SUSPENDED | _CREATE_NO_WINDOW,
        None,
        str(probe_script.parent),
        ctypes.byref(startup),
        ctypes.byref(process),
    ):
        _raise_last_error("CreateProcessWithLogonW")

    finished = False
    exit_code_value: int | None = None
    primary_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    try:
        token = wintypes.HANDLE()
        if not _ADVAPI32.OpenProcessToken(
            process.hProcess,
            _TOKEN_QUERY | _TOKEN_DUPLICATE,
            ctypes.byref(token),
        ):
            _raise_last_error("OpenProcessToken(child)")
        try:
            _assert_standard_token(token.value, account.sid)
        finally:
            _close_handle(token.value)

        if _KERNEL32.ResumeThread(process.hThread) == 0xFFFFFFFF:
            _raise_last_error("ResumeThread")
        wait_result = _KERNEL32.WaitForSingleObject(process.hProcess, timeout_ms)
        if wait_result == _WAIT_TIMEOUT:
            raise TimeoutError("real-identity child probe timed out")
        try:
            print("PROBE-OUTPUT", diagnostic.read_text(encoding="utf-8")[:2000])
        except OSError as unreadable:
            print("PROBE-OUTPUT missing:", unreadable)
        if wait_result != _WAIT_OBJECT_0:
            _raise_last_error("WaitForSingleObject")
        exit_code = wintypes.DWORD(_STILL_ACTIVE)
        if not _KERNEL32.GetExitCodeProcess(
            process.hProcess, ctypes.byref(exit_code)
        ):
            _raise_last_error("GetExitCodeProcess")
        if exit_code.value == _STILL_ACTIVE:
            raise AssertionError("child remained active after a signaled wait")
        finished = True
        exit_code_value = exit_code.value
    except BaseException as exc:
        primary_error = exc
    finally:
        if not finished:
            try:
                terminated = bool(
                    _KERNEL32.TerminateProcess(
                        process.hProcess, ACCESS_RESULT_UNEXPECTED
                    )
                )
                termination_error = (
                    None
                    if terminated
                    else ctypes.WinError(
                        ctypes.get_last_error(), "TerminateProcess(cleanup)"
                    )
                )
                cleanup_wait = _KERNEL32.WaitForSingleObject(
                    process.hProcess, 5_000
                )
                if cleanup_wait != _WAIT_OBJECT_0:
                    errors: list[BaseException] = []
                    if termination_error is not None:
                        errors.append(termination_error)
                    if cleanup_wait == _WAIT_TIMEOUT:
                        errors.append(
                            TimeoutError("child cleanup did not reach termination")
                        )
                    else:
                        errors.append(
                            ctypes.WinError(
                                ctypes.get_last_error(),
                                "WaitForSingleObject(cleanup)",
                            )
                        )
                    raise BaseExceptionGroup(
                        "child termination and wait failed", errors
                    )
                finished = True
            except BaseException as exc:
                cleanup_errors.append(exc)
        for handle in (process.hThread, process.hProcess):
            try:
                _close_handle(handle)
            except BaseException as exc:
                cleanup_errors.append(exc)

    if primary_error is not None and cleanup_errors:
        raise BaseExceptionGroup(
            "child probe and cleanup both failed",
            [primary_error, *cleanup_errors],
        )
    if cleanup_errors:
        raise BaseExceptionGroup("child probe cleanup failed", cleanup_errors)
    if primary_error is not None:
        raise primary_error
    if exit_code_value is None:
        raise AssertionError("child probe produced no exit status")
    return exit_code_value


@contextlib.contextmanager
def enabled_restore_privilege() -> Iterator[None]:
    token = wintypes.HANDLE()
    if not _ADVAPI32.OpenProcessToken(
        _KERNEL32.GetCurrentProcess(),
        _TOKEN_QUERY | _TOKEN_ADJUST_PRIVILEGES,
        ctypes.byref(token),
    ):
        _raise_last_error("OpenProcessToken(privilege)")
    previous = _TOKEN_PRIVILEGES()
    previous_size = wintypes.DWORD()
    requested = _TOKEN_PRIVILEGES()
    requested.PrivilegeCount = 1
    if not _ADVAPI32.LookupPrivilegeValueW(
        None, "SeRestorePrivilege", ctypes.byref(requested.Privileges[0].Luid)
    ):
        _close_handle(token.value)
        _raise_last_error("LookupPrivilegeValueW")
    requested.Privileges[0].Attributes = _SE_PRIVILEGE_ENABLED
    try:
        ctypes.set_last_error(0)
        if not _ADVAPI32.AdjustTokenPrivileges(
            token,
            False,
            ctypes.byref(requested),
            ctypes.sizeof(previous),
            ctypes.byref(previous),
            ctypes.byref(previous_size),
        ):
            _raise_last_error("AdjustTokenPrivileges(enable)")
        if ctypes.get_last_error() == _ERROR_NOT_ALL_ASSIGNED:
            raise PermissionError("SeRestorePrivilege is not assigned")
        yield
    finally:
        restore_error: BaseException | None = None
        if previous_size.value:
            if not _ADVAPI32.AdjustTokenPrivileges(
                token,
                False,
                ctypes.byref(previous),
                0,
                None,
                None,
            ):
                restore_error = ctypes.WinError(
                    ctypes.get_last_error(), "AdjustTokenPrivileges(restore)"
                )
        try:
            _close_handle(token.value)
        except BaseException as exc:
            restore_error = restore_error or exc
        if restore_error is not None:
            raise restore_error


def _profile_sddl(
    profile: Literal["confidential", "integrity_only"],
    service_sid: str,
    *,
    directory: bool,
    extra_read_sid: str | None = None,
    owner: str = "SY",
    service_mask: int | None = None,
) -> str:
    read_mask = _DIRECTORY_READ_MASK if directory else _FILE_READ_MASK
    effective_service_mask = read_mask if service_mask is None else service_mask
    aces = [
        "(A;;FA;;;SY)",
        "(A;;FA;;;BA)",
        f"(A;;0x{effective_service_mask:08x};;;{service_sid})",
    ]
    if profile == "integrity_only":
        aces.append(f"(A;;0x{read_mask:08x};;;AU)")
    elif profile != "confidential":
        raise ValueError("unknown profile")
    if extra_read_sid is not None:
        aces.append(f"(A;;0x{read_mask:08x};;;{extra_read_sid})")
    return f"O:{owner}D:P" + "".join(aces)


def apply_profile(
    path: Path,
    profile: Literal["confidential", "integrity_only"],
    service_sid: str,
    *,
    directory: bool,
    extra_read_sid: str | None = None,
    owner: str = "SY",
    service_mask: int | None = None,
) -> None:
    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.DWORD()
    if not _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        _profile_sddl(
            profile,
            service_sid,
            directory=directory,
            extra_read_sid=extra_read_sid,
            owner=owner,
            service_mask=service_mask,
        ),
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        _raise_last_error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    handle: int | None = None
    try:
        owner = ctypes.c_void_p()
        owner_defaulted = wintypes.BOOL()
        dacl_present = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        dacl_defaulted = wintypes.BOOL()
        if not _ADVAPI32.GetSecurityDescriptorOwner(
            descriptor, ctypes.byref(owner), ctypes.byref(owner_defaulted)
        ):
            _raise_last_error("GetSecurityDescriptorOwner")
        if not _ADVAPI32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        ):
            _raise_last_error("GetSecurityDescriptorDacl")
        if owner_defaulted or not dacl_present or not dacl or dacl_defaulted:
            raise AssertionError("fixture descriptor is not explicit and complete")
        handle = _open_path(
            path, _READ_CONTROL | _WRITE_DAC | _WRITE_OWNER, directory=directory
        )
        with enabled_restore_privilege():
            result = _ADVAPI32.SetSecurityInfo(
                handle,
                _SE_FILE_OBJECT,
                _OWNER_SECURITY_INFORMATION
                | _DACL_SECURITY_INFORMATION
                | _PROTECTED_DACL_SECURITY_INFORMATION,
                owner,
                None,
                dacl,
                None,
            )
        if result:
            raise OSError(result, "SetSecurityInfo")
    finally:
        if handle:
            _close_handle(handle)
        if descriptor.value:
            _KERNEL32.LocalFree(descriptor)


def assert_exact_profile(
    path: Path,
    profile: Literal["confidential", "integrity_only"],
    service_sid: str,
    *,
    directory: bool,
) -> None:
    handle = _open_path(path, _READ_CONTROL, directory=directory)
    descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    try:
        result = _ADVAPI32.GetSecurityInfo(
            handle,
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            raise OSError(result, "GetSecurityInfo")
        if not owner.value or not dacl.value or not descriptor.value:
            raise AssertionError("owner or DACL is absent")

        descriptor_dacl = ctypes.c_void_p()
        dacl_present = wintypes.BOOL()
        dacl_defaulted = wintypes.BOOL()
        if not _ADVAPI32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(descriptor_dacl),
            ctypes.byref(dacl_defaulted),
        ):
            _raise_last_error("GetSecurityDescriptorDacl")
        if (
            not dacl_present
            or dacl_defaulted
            or not descriptor_dacl.value
            or descriptor_dacl.value != dacl.value
        ):
            raise AssertionError("DACL is absent, defaulted, or inconsistent")

        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not _ADVAPI32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            _raise_last_error("GetSecurityDescriptorControl")
        if not control.value & _SE_DACL_PROTECTED:
            raise AssertionError("DACL is not protected")

        acl_information = _ACL_SIZE_INFORMATION()
        if not _ADVAPI32.GetAclInformation(
            dacl,
            ctypes.byref(acl_information),
            ctypes.sizeof(acl_information),
            _ACL_SIZE_INFORMATION_CLASS,
        ):
            _raise_last_error("GetAclInformation")

        expected_strings = ["S-1-5-18", "S-1-5-32-544", service_sid]
        expected_masks = [_FILE_ALL_ACCESS, _FILE_ALL_ACCESS]
        read_mask = _DIRECTORY_READ_MASK if directory else _FILE_READ_MASK
        expected_masks.append(read_mask)
        if profile == "integrity_only":
            expected_strings.append("S-1-5-11")
            expected_masks.append(read_mask)
        elif profile != "confidential":
            raise ValueError("unknown profile")
        if acl_information.AceCount != len(expected_strings):
            raise AssertionError("DACL contains an unexpected number of ACEs")

        with contextlib.ExitStack() as stack:
            expected_sids = [
                stack.enter_context(_string_sid(value)) for value in expected_strings
            ]
            if not _ADVAPI32.EqualSid(owner, expected_sids[0]):
                raise AssertionError("owner is not SYSTEM")
            for index, (expected_sid, expected_mask) in enumerate(
                zip(expected_sids, expected_masks, strict=True)
            ):
                ace_pointer = ctypes.c_void_p()
                if not _ADVAPI32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                    _raise_last_error("GetAce")
                ace = ctypes.cast(
                    ace_pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)
                ).contents
                if ace.Header.AceType != _ACCESS_ALLOWED_ACE_TYPE:
                    raise AssertionError("DACL contains a non-allow ACE")
                if ace.Header.AceFlags != 0:
                    raise AssertionError("DACL contains inherited or flagged ACEs")
                if ace.Mask != expected_mask:
                    raise AssertionError(
                        "DACL ACE mask differs from the closed profile"
                    )
                sid_pointer = ace_pointer.value + _ACCESS_ALLOWED_ACE.SidStart.offset
                if not _ADVAPI32.IsValidSid(sid_pointer):
                    raise AssertionError("DACL ACE contains an invalid SID")
                if not _ADVAPI32.EqualSid(sid_pointer, expected_sid):
                    raise AssertionError("DACL ACE order or SID differs")
    finally:
        if descriptor.value:
            _KERNEL32.LocalFree(descriptor)
        _close_handle(handle)


def assert_supported_volume(path: Path) -> None:
    root = Path(path.anchor)
    filesystem = ctypes.create_unicode_buffer(64)
    flags = wintypes.DWORD()
    if not _KERNEL32.GetVolumeInformationW(
        str(root), None, 0, None, None, ctypes.byref(flags), filesystem, len(filesystem)
    ):
        _raise_last_error("GetVolumeInformationW")
    if filesystem.value.casefold() != "ntfs":
        raise AssertionError("real-identity fixture volume is not NTFS")
    if not flags.value & _FILE_PERSISTENT_ACLS:
        raise AssertionError("real-identity fixture volume lacks persistent ACLs")


def _child_open(operation: str, path: Path, *, directory: bool) -> int:
    if operation == "noop":
        return ACCESS_RESULT_ALLOWED
    access_by_operation = {
        "read": _GENERIC_READ,
        "write": _GENERIC_WRITE,
        "append": _FILE_APPEND_DATA,
        "delete": _DELETE,
        "delete_child": _FILE_DELETE_CHILD,
        "write_dac": _WRITE_DAC,
    }
    if operation == "create_child":
        target = path / "unexpected.secret"
        handle = _KERNEL32.CreateFileW(
            str(target),
            _GENERIC_WRITE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _CREATE_NEW,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
    else:
        access = access_by_operation.get(operation)
        if access is None:
            return ACCESS_RESULT_UNEXPECTED
        flags = _FILE_FLAG_BACKUP_SEMANTICS if directory else _FILE_ATTRIBUTE_NORMAL
        handle = _KERNEL32.CreateFileW(
            str(path),
            access,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _OPEN_EXISTING,
            flags,
            None,
        )
    if handle == _INVALID_HANDLE_VALUE:
        return (
            ACCESS_RESULT_DENIED
            if ctypes.get_last_error() == _ERROR_ACCESS_DENIED
            else ACCESS_RESULT_UNEXPECTED
        )
    try:
        if operation == "read" and not directory:
            payload = ctypes.create_string_buffer(1)
            received = wintypes.DWORD()
            if not _KERNEL32.ReadFile(
                handle, payload, 1, ctypes.byref(received), None
            ) or received.value != 1:
                return ACCESS_RESULT_UNEXPECTED
        elif operation == "read" and directory:
            find_data = _WIN32_FIND_DATAW()
            find_handle = _KERNEL32.FindFirstFileW(
                str(path / "*"), ctypes.byref(find_data)
            )
            if find_handle == _INVALID_HANDLE_VALUE:
                return (
                    ACCESS_RESULT_DENIED
                    if ctypes.get_last_error() == _ERROR_ACCESS_DENIED
                    else ACCESS_RESULT_UNEXPECTED
                )
            if not _KERNEL32.FindClose(find_handle):
                return ACCESS_RESULT_UNEXPECTED
        return ACCESS_RESULT_ALLOWED
    finally:
        _KERNEL32.CloseHandle(handle)


def _child_main(arguments: list[str]) -> int:
    if len(arguments) != 4 or arguments[0] != "--child":
        return ACCESS_RESULT_UNEXPECTED
    operation, raw_path, kind = arguments[1:]
    if kind not in {"file", "directory"}:
        return ACCESS_RESULT_UNEXPECTED
    return _child_open(operation, Path(raw_path), directory=kind == "directory")


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
