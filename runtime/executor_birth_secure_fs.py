"""Handle-bound filesystem primitives for Executor Birth.

This module deliberately contains no provisioning policy and no key handling.
It supplies the small, closed set of low-level operations used by the Birth
loaders and, in later increments, by the installer-owned provisioner.
"""
from __future__ import annotations

import contextlib
import ctypes
import errno
import math
import os
import stat
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, Sequence


_MAX_COMPONENT_BYTES = 256
_MAX_RELATIVE_BYTES = 1024
_LOCK_BYTE = b"0"
_LOCK_DELAYS = (0.005, 0.010, 0.020, 0.040, 0.080, 0.100)


class BirthSecureFSError(RuntimeError):
    """Stable public failure without paths, ACLs or platform diagnostics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _ObjectIdentity:
    volume: str
    object_id: str


@dataclass(frozen=True, slots=True)
class _InventoryEntry:
    name: str
    identity: _ObjectIdentity
    directory: bool
    links: int


@dataclass(frozen=True, slots=True)
class _PlatformIdentity:
    posix_uid: int | None
    windows_service_sid: str | None

    def __post_init__(self) -> None:
        if self.posix_uid is not None and (
            isinstance(self.posix_uid, bool) or self.posix_uid < 0
        ):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        sid = self.windows_service_sid
        if sid is not None and (
            not isinstance(sid, str)
            or not sid.startswith("S-1-")
            or sid.casefold()
            in {"s-1-5-18", "s-1-5-32-544", "s-1-5-11"}
        ):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")


class _AuthenticatedRootDescriptor:
    __slots__ = ("handles", "identity", "root_path", "_adopted")

    def __init__(
        self,
        token: object,
        handles: list[int],
        root_path: str,
        identity: _PlatformIdentity,
    ) -> None:
        if token is not _DESCRIPTOR_TOKEN or not handles:
            raise TypeError("private descriptor")
        self.handles = handles
        self.root_path = root_path
        self.identity = identity
        self._adopted = False


def _relative_components(value: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    total = 0
    result: list[str] = []
    for component in value:
        if not isinstance(component, str) or not component:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        if component != unicodedata.normalize("NFC", component):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        stem = component.split(".", 1)[0].casefold()
        reserved = stem in {"con", "prn", "aux", "nul"}
        reserved = reserved or (
            len(stem) == 4
            and stem[:3] in {"com", "lpt"}
            and stem[3] in "123456789"
        )
        if (
            component in {".", ".."}
            or "\0" in component
            or "/" in component
            or "\\" in component
            or ":" in component
            or "*" in component
            or "?" in component
            or component.endswith((".", " "))
            or any(ord(character) < 32 for character in component)
            or reserved
        ):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        try:
            encoded = component.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        if len(encoded) > _MAX_COMPONENT_BYTES:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        total += len(encoded) + (1 if result else 0)
        if total > _MAX_RELATIVE_BYTES:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        result.append(component)
    return tuple(result)


def _posix_snapshot(fd: int) -> tuple[int, ...]:
    value = os.fstat(fd)
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _posix_identity(fd: int) -> _ObjectIdentity:
    value = os.fstat(fd)
    return _ObjectIdentity(f"{value.st_dev:x}", f"{value.st_ino:x}")


def _verify_posix_directory(
    fd: int, *, exact_private: bool, expected_uid: int | None
) -> None:
    value = os.fstat(fd)
    if not stat.S_ISDIR(value.st_mode) or (
        expected_uid is not None and value.st_uid != expected_uid
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    mode = stat.S_IMODE(value.st_mode)
    if (exact_private and mode != 0o700) or (not exact_private and mode & 0o022):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")


def _verify_posix_file(
    fd: int, *, exact_private: bool, expected_uid: int | None
) -> None:
    value = os.fstat(fd)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or (expected_uid is not None and value.st_uid != expected_uid)
    ):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    mode = stat.S_IMODE(value.st_mode)
    if (exact_private and mode != 0o600) or (not exact_private and mode & 0o022):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")


def _open_posix_root(
    path: Path, *, exact_private: bool, expected_uid: int | None
) -> tuple[list[int], str]:
    raw = os.fspath(path)
    if "\0" in raw:
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    absolute = os.path.abspath(raw)
    drive, tail = os.path.splitdrive(absolute)
    if drive or not tail.startswith(os.sep):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    components = tuple(item for item in tail.split(os.sep) if item)
    if any(item in {".", ".."} for item in Path(raw).parts):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    opened: list[int] = []
    try:
        current = os.open(os.sep, flags)
        opened.append(current)
        for component in components:
            current = os.open(component, flags, dir_fd=current)
            opened.append(current)
        _verify_posix_directory(
            opened[-1], exact_private=exact_private, expected_uid=expected_uid
        )
        return opened, absolute
    except BirthSecureFSError:
        for fd in reversed(opened):
            os.close(fd)
        raise
    except OSError as exc:
        for fd in reversed(opened):
            os.close(fd)
        raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc


if os.name == "nt":  # pragma: no cover - definitions exercised by Windows CI
    from ctypes import wintypes

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _FILE_READ_DATA = 0x0001
    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_TRAVERSE = 0x0020
    _FILE_READ_ATTRIBUTES = 0x0080
    _DELETE = 0x00010000
    _READ_CONTROL = 0x00020000
    _WRITE_DAC = 0x00040000
    _WRITE_OWNER = 0x00080000
    _SYNCHRONIZE = 0x00100000
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _CREATE_NEW = 1
    _OPEN_EXISTING = 3
    _FILE_FLAG_WRITE_THROUGH = 0x80000000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_PERSISTENT_ACLS = 0x00000008
    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_PATH_NOT_FOUND = 3
    _ERROR_ACCESS_DENIED = 5
    _ERROR_NO_MORE_FILES = 18
    _ERROR_SHARING_VIOLATION = 32
    _ERROR_LOCK_VIOLATION = 33
    _ERROR_NOT_SUPPORTED = 50
    _ERROR_NOT_ALL_ASSIGNED = 1300
    _ERROR_PRIVILEGE_NOT_HELD = 1314
    _ERROR_FILE_EXISTS = 80
    _ERROR_ALREADY_EXISTS = 183
    _ERROR_NOT_SAME_DEVICE = 17
    _FILE_STANDARD_INFO_CLASS = 1
    _FILE_RENAME_INFO_CLASS = 3
    _FILE_DISPOSITION_INFO_EX_CLASS = 21
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    _FILE_ID_INFO_CLASS = 18
    _FILE_ID_EXTD_DIRECTORY_INFO_CLASS = 19
    _FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS = 20
    _LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
    _LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
    _FILE_DISPOSITION_FLAG_DELETE = 0x00000001
    _FILE_DISPOSITION_FLAG_POSIX_SEMANTICS = 0x00000002
    _FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE = 0x00000010
    _TOKEN_QUERY = 0x0008
    _TOKEN_ADJUST_PRIVILEGES = 0x0020
    _SE_PRIVILEGE_ENABLED = 0x00000002
    _TOKEN_USER_CLASS = 1
    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SDDL_REVISION_1 = 1

    class _FILE_STANDARD_INFO(ctypes.Structure):
        _fields_ = [
            ("AllocationSize", ctypes.c_longlong),
            ("EndOfFile", ctypes.c_longlong),
            ("NumberOfLinks", wintypes.DWORD),
            ("DeletePending", wintypes.BOOLEAN),
            ("Directory", wintypes.BOOLEAN),
        ]

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    class _FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", _FILE_ID_128)]

    class _OVERLAPPED_UNION_OFFSET(ctypes.Structure):
        _fields_ = [("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD)]

    class _OVERLAPPED_UNION(ctypes.Union):
        _fields_ = [("offset", _OVERLAPPED_UNION_OFFSET), ("Pointer", ctypes.c_void_p)]

    class _OVERLAPPED(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("union", _OVERLAPPED_UNION),
            ("hEvent", wintypes.HANDLE),
        ]

    class _FILE_RENAME_INFO_HEADER(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _FILE_DISPOSITION_INFO_EX(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD)]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class _LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", wintypes.DWORD),
            ("Privileges", _LUID_AND_ATTRIBUTES * 1),
        ]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _FILE_ID_EXTD_DIR_INFO(ctypes.Structure):
        _fields_ = [
            ("NextEntryOffset", wintypes.DWORD),
            ("FileIndex", wintypes.DWORD),
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("EndOfFile", ctypes.c_longlong),
            ("AllocationSize", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
            ("FileNameLength", wintypes.DWORD),
            ("EaSize", wintypes.DWORD),
            ("ReparsePointTag", wintypes.DWORD),
            ("FileId", _FILE_ID_128),
            ("FileName", wintypes.WCHAR * 1),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)
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
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _KERNEL32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _KERNEL32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _KERNEL32.GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _KERNEL32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _KERNEL32.ReadFile.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    _KERNEL32.ReadFile.restype = wintypes.BOOL
    _KERNEL32.WriteFile.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    _KERNEL32.WriteFile.restype = wintypes.BOOL
    _KERNEL32.SetFilePointerEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    )
    _KERNEL32.SetFilePointerEx.restype = wintypes.BOOL
    _KERNEL32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    _KERNEL32.FlushFileBuffers.restype = wintypes.BOOL
    _KERNEL32.LockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    )
    _KERNEL32.LockFileEx.restype = wintypes.BOOL
    _KERNEL32.UnlockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    )
    _KERNEL32.UnlockFileEx.restype = wintypes.BOOL
    _KERNEL32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _KERNEL32.SetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.CreateDirectoryW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    )
    _KERNEL32.CreateDirectoryW.restype = wintypes.BOOL
    _KERNEL32.GetVolumeInformationByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    _KERNEL32.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    _KERNEL32.GetCurrentProcess.argtypes = ()
    _KERNEL32.GetCurrentProcess.restype = wintypes.HANDLE
    _KERNEL32.LocalFree.argtypes = (ctypes.c_void_p,)
    _KERNEL32.LocalFree.restype = ctypes.c_void_p
    _KERNEL32.SetLastError.argtypes = (wintypes.DWORD,)
    _KERNEL32.SetLastError.restype = None
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
    _ADVAPI32.ConvertSidToStringSidW.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    _ADVAPI32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    )
    _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _ADVAPI32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    )
    _ADVAPI32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
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


def _win_close(handle: int) -> None:
    if os.name == "nt" and handle not in {None, _INVALID_HANDLE_VALUE}:
        _KERNEL32.CloseHandle(handle)


def _win_error(operation: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, operation)


def _win_open_path(
    path: str,
    *,
    directory: bool,
    writable: bool = False,
    delete: bool = False,
    create: bool = False,
    security_attributes: object | None = None,
    security_write: bool = False,
    generic_read: bool = False,
) -> int:
    access = _FILE_READ_ATTRIBUTES | _READ_CONTROL | _SYNCHRONIZE
    access |= _FILE_LIST_DIRECTORY | _FILE_TRAVERSE if directory else _FILE_READ_DATA
    if writable:
        access |= _GENERIC_READ | _GENERIC_WRITE
    elif generic_read:
        access |= _GENERIC_READ
    if delete:
        access |= _DELETE
    if security_write:
        access |= _WRITE_DAC | _WRITE_OWNER
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    if writable or delete:
        flags |= _FILE_FLAG_WRITE_THROUGH
    handle = _KERNEL32.CreateFileW(
        path,
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        security_attributes,
        _CREATE_NEW if create else _OPEN_EXISTING,
        flags,
        None,
    )
    if handle in {None, _INVALID_HANDLE_VALUE}:
        raise _win_error("CreateFileW")
    return handle


def _win_info(handle: int) -> tuple[_ObjectIdentity, int, int, bool, bool, int]:
    standard = _FILE_STANDARD_INFO()
    tagged = _FILE_ATTRIBUTE_TAG_INFO()
    identity = _FILE_ID_INFO()
    for info_class, target in (
        (_FILE_STANDARD_INFO_CLASS, standard),
        (_FILE_ATTRIBUTE_TAG_INFO_CLASS, tagged),
        (_FILE_ID_INFO_CLASS, identity),
    ):
        if not _KERNEL32.GetFileInformationByHandleEx(
            handle, info_class, ctypes.byref(target), ctypes.sizeof(target)
        ):
            raise _win_error("GetFileInformationByHandleEx")
    object_identity = _ObjectIdentity(
        f"{int(identity.VolumeSerialNumber):016x}",
        bytes(identity.FileId.Identifier).hex(),
    )
    return (
        object_identity,
        int(tagged.FileAttributes),
        int(standard.NumberOfLinks),
        bool(standard.DeletePending),
        bool(standard.Directory),
        int(standard.EndOfFile),
    )


def _win_final_path(handle: int) -> str:
    needed = _KERNEL32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not needed:
        raise _win_error("GetFinalPathNameByHandleW")
    buffer = ctypes.create_unicode_buffer(needed + 1)
    written = _KERNEL32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not written or written >= len(buffer):
        raise _win_error("GetFinalPathNameByHandleW")
    return _win_normalize_comparison_path(buffer.value)


def _win_normalize_comparison_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value)).rstrip("\\/")


def _win_prefixes(path: str) -> tuple[str, ...]:
    absolute = os.path.abspath(path)
    drive, tail = os.path.splitdrive(absolute)
    if not drive or not tail.startswith(("\\", "/")):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    root = drive + "\\"
    result = [root]
    current = root
    for component in (item for item in tail.replace("/", "\\").split("\\") if item):
        current = os.path.join(current, component)
        result.append(current)
    return tuple(result)


def _verify_win_object(handle: int, expected_path: str, *, directory: bool) -> tuple:
    value = _win_info(handle)
    _, attributes, links, delete_pending, is_directory, size = value
    if (
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or delete_pending
        or is_directory != directory
        or (not directory and links != 1)
        or size < 0
    ):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    if _win_final_path(handle) != _win_normalize_comparison_path(expected_path):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    return value


def _open_win_root(path: Path) -> tuple[list[int], str]:
    absolute = os.path.abspath(os.fspath(path))
    opened: list[int] = []
    try:
        for prefix in _win_prefixes(absolute):
            handle = _win_open_path(prefix, directory=True)
            opened.append(handle)
            _verify_win_object(handle, prefix, directory=True)
        return opened, absolute
    except BirthSecureFSError:
        for handle in reversed(opened):
            _win_close(handle)
        raise
    except OSError as exc:
        for handle in reversed(opened):
            _win_close(handle)
        raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc


def _win_require_supported_volume(handle: int) -> None:
    flags = wintypes.DWORD()
    filesystem = ctypes.create_unicode_buffer(32)
    if not _KERNEL32.GetVolumeInformationByHandleW(
        handle,
        None,
        0,
        None,
        None,
        ctypes.byref(flags),
        filesystem,
        len(filesystem),
    ):
        raise BirthSecureFSError(
            "birth_provisioning_atomic_install_unsupported"
        ) from _win_error("GetVolumeInformationByHandleW")
    if filesystem.value.casefold() != "ntfs" or not flags.value & _FILE_PERSISTENT_ACLS:
        raise BirthSecureFSError("birth_provisioning_atomic_install_unsupported")


def _windows_service_sid_for_current_process() -> str:
    """Return the real process-token SID for isolated Windows primitive tests."""
    if os.name != "nt":
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    token = wintypes.HANDLE()
    if not _ADVAPI32.OpenProcessToken(
        _KERNEL32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise BirthSecureFSError("birth_provisioning_io_unavailable") from _win_error(
            "OpenProcessToken"
        )
    try:
        required = wintypes.DWORD()
        _ADVAPI32.GetTokenInformation(
            token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(required)
        )
        if not required.value:
            raise _win_error("GetTokenInformation")
        buffer = ctypes.create_string_buffer(required.value)
        if not _ADVAPI32.GetTokenInformation(
            token,
            _TOKEN_USER_CLASS,
            buffer,
            len(buffer),
            ctypes.byref(required),
        ):
            raise _win_error("GetTokenInformation")
        token_user = _TOKEN_USER.from_buffer(buffer)
        encoded = wintypes.LPWSTR()
        if not _ADVAPI32.ConvertSidToStringSidW(
            token_user.Sid, ctypes.byref(encoded)
        ):
            raise _win_error("ConvertSidToStringSidW")
        try:
            return encoded.value
        finally:
            _KERNEL32.LocalFree(ctypes.cast(encoded, ctypes.c_void_p))
    except OSError as exc:
        raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
    finally:
        _win_close(token.value)


@contextlib.contextmanager
def _win_restore_privilege() -> Iterator[None]:
    token = wintypes.HANDLE()
    if not _ADVAPI32.OpenProcessToken(
        _KERNEL32.GetCurrentProcess(),
        _TOKEN_QUERY | _TOKEN_ADJUST_PRIVILEGES,
        ctypes.byref(token),
    ):
        raise BirthSecureFSError("birth_provisioning_elevation_required") from _win_error(
            "OpenProcessToken"
        )
    previous = _TOKEN_PRIVILEGES()
    previous_size = wintypes.DWORD(ctypes.sizeof(previous))
    try:
        luid = _LUID()
        if not _ADVAPI32.LookupPrivilegeValueW(
            None, "SeRestorePrivilege", ctypes.byref(luid)
        ):
            raise _win_error("LookupPrivilegeValueW")
        requested = _TOKEN_PRIVILEGES()
        requested.PrivilegeCount = 1
        requested.Privileges[0].Luid = luid
        requested.Privileges[0].Attributes = _SE_PRIVILEGE_ENABLED
        _KERNEL32.SetLastError(0)
        if not _ADVAPI32.AdjustTokenPrivileges(
            token,
            False,
            ctypes.byref(requested),
            ctypes.sizeof(previous),
            ctypes.byref(previous),
            ctypes.byref(previous_size),
        ):
            raise _win_error("AdjustTokenPrivileges")
        if ctypes.get_last_error() == _ERROR_NOT_ALL_ASSIGNED:
            raise BirthSecureFSError("birth_provisioning_elevation_required")
    except BirthSecureFSError:
        _win_close(token.value)
        raise
    except OSError as exc:
        _win_close(token.value)
        raise BirthSecureFSError("birth_provisioning_elevation_required") from exc
    try:
        yield
    finally:
        if previous.PrivilegeCount:
            _KERNEL32.SetLastError(0)
            restored = _ADVAPI32.AdjustTokenPrivileges(
                token,
                False,
                ctypes.byref(previous),
                0,
                None,
                None,
            )
            restore_error = ctypes.get_last_error()
            if not restored or restore_error == _ERROR_NOT_ALL_ASSIGNED:
                failure = (
                    _win_error("AdjustTokenPrivileges(restore)")
                    if not restored
                    else OSError(restore_error, "AdjustTokenPrivileges(restore)")
                )
                _win_close(token.value)
                raise BirthSecureFSError(
                    "birth_provisioning_elevation_required"
                ) from failure
        _win_close(token.value)


def _win_sddl(
    profile: Literal["confidential", "integrity_only"],
    *,
    directory: bool,
    service_sid: str,
) -> str:
    if (
        not isinstance(service_sid, str)
        or not service_sid.startswith("S-1-")
        or service_sid.casefold()
        in {"s-1-5-18", "s-1-5-32-544", "s-1-5-11"}
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    service_mask = "0x001200a9" if directory else "0x00120089"
    aces = ["(A;;FA;;;SY)", "(A;;FA;;;BA)", f"(A;;{service_mask};;;{service_sid})"]
    if profile == "integrity_only":
        aces.append(f"(A;;{service_mask};;;AU)")
    elif profile != "confidential":
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    return "O:SYD:P" + "".join(aces)


@contextlib.contextmanager
def _win_security_attributes(
    profile: Literal["confidential", "integrity_only"],
    *,
    directory: bool,
    service_sid: str,
) -> Iterator[tuple[_SECURITY_ATTRIBUTES, int]]:
    descriptor = ctypes.c_void_p()
    size = wintypes.DWORD()
    if not _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        _win_sddl(profile, directory=directory, service_sid=service_sid),
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(size),
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe") from _win_error(
            "ConvertStringSecurityDescriptorToSecurityDescriptorW"
        )
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
    )
    try:
        yield attributes, descriptor.value
    finally:
        _KERNEL32.LocalFree(descriptor)


def _win_descriptor_sddl(descriptor: int) -> str:
    encoded = wintypes.LPWSTR()
    length = wintypes.DWORD()
    information = _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION
    if not _ADVAPI32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor,
        _SDDL_REVISION_1,
        information,
        ctypes.byref(encoded),
        ctypes.byref(length),
    ):
        raise _win_error("ConvertSecurityDescriptorToStringSecurityDescriptorW")
    try:
        return encoded.value
    finally:
        _KERNEL32.LocalFree(ctypes.cast(encoded, ctypes.c_void_p))


def _win_apply_and_verify_security(handle: int, expected_descriptor: int) -> None:
    owner = ctypes.c_void_p()
    owner_defaulted = wintypes.BOOL()
    dacl_present = wintypes.BOOL()
    dacl = ctypes.c_void_p()
    dacl_defaulted = wintypes.BOOL()
    if not _ADVAPI32.GetSecurityDescriptorOwner(
        expected_descriptor, ctypes.byref(owner), ctypes.byref(owner_defaulted)
    ) or not _ADVAPI32.GetSecurityDescriptorDacl(
        expected_descriptor,
        ctypes.byref(dacl_present),
        ctypes.byref(dacl),
        ctypes.byref(dacl_defaulted),
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe") from _win_error(
            "GetSecurityDescriptor"
        )
    if owner_defaulted or not dacl_present or not dacl or dacl_defaulted:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    information = (
        _OWNER_SECURITY_INFORMATION
        | _DACL_SECURITY_INFORMATION
        | _PROTECTED_DACL_SECURITY_INFORMATION
    )
    result = _ADVAPI32.SetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        information,
        owner,
        None,
        dacl,
        None,
    )
    if result:
        code = (
            "birth_provisioning_elevation_required"
            if result in {_ERROR_ACCESS_DENIED, _ERROR_PRIVILEGE_NOT_HELD}
            else "birth_provisioning_acl_unsafe"
        )
        raise BirthSecureFSError(code) from OSError(result, "SetSecurityInfo")

    _win_verify_security(handle, expected_descriptor)


def _win_verify_security(handle: int, expected_descriptor: int) -> None:
    actual_descriptor = ctypes.c_void_p()
    result = _ADVAPI32.GetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        None,
        None,
        None,
        None,
        ctypes.byref(actual_descriptor),
    )
    if result:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe") from OSError(
            result, "GetSecurityInfo"
        )
    try:
        expected = _win_descriptor_sddl(expected_descriptor)
        actual = _win_descriptor_sddl(actual_descriptor.value)
        if actual.casefold() != expected.casefold():
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    except OSError as exc:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe") from exc
    finally:
        _KERNEL32.LocalFree(actual_descriptor)


def _win_dispose_created(handle: int) -> None:
    disposition = _FILE_DISPOSITION_INFO_EX(
        _FILE_DISPOSITION_FLAG_DELETE
        | _FILE_DISPOSITION_FLAG_POSIX_SEMANTICS
        | _FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE
    )
    if not _KERNEL32.SetFileInformationByHandle(
        handle,
        _FILE_DISPOSITION_INFO_EX_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise _win_error("SetFileInformationByHandle(FileDispositionInfoEx)")


class _SecureDirectoryHandle:
    """Opaque directory capability; it never reveals an OS path or raw handle."""

    __slots__ = ("_session", "_components")

    def __init__(self, session: "_SecureRootSession", components: tuple[str, ...]) -> None:
        self._session = session
        self._components = components

    def read_file(
        self, name: str, *, maximum: int, exact_private: bool = True
    ) -> bytes:
        return self._session.read_file(
            self._components + _relative_components((name,)),
            maximum=maximum,
            exact_private=exact_private,
        )

    def inventory(self) -> tuple[str, ...]:
        return self._session.inventory(self._components)

    def open_directory(self, name: str, *, exact_private: bool = True) -> "_SecureDirectoryHandle":
        return self._session.open_directory(
            self._components + _relative_components((name,)), exact_private=exact_private
        )


class _SecureRootSession:
    """Root-bound capability adopted from an authenticated descriptor."""

    __slots__ = (
        "_closed",
        "_authoritative",
        "_exact_private",
        "_directories",
        "_directory_profiles",
        "_file_profiles",
        "_handles",
        "_lock_stack",
        "_expected_uid",
        "_root_path",
        "_root_name",
        "_root_parent_handle",
        "_service_sid",
    )

    def __init__(
        self,
        token: object,
        handles: list[int],
        root_path: str,
        *,
        exact_private: bool,
        service_sid: str | None,
        expected_uid: int | None,
        authoritative: bool,
    ) -> None:
        if token is not _SESSION_TOKEN:
            raise TypeError("private constructor")
        self._handles = handles
        self._directories = {(): handles[-1]}
        self._directory_profiles = {(): "confidential"}
        self._file_profiles: dict[tuple[str, ...], str] = {}
        self._root_path = root_path
        self._root_name = os.path.basename(root_path.rstrip(os.sep))
        self._root_parent_handle = handles[-2] if len(handles) > 1 else None
        self._exact_private = exact_private
        self._service_sid = service_sid
        self._expected_uid = expected_uid
        self._authoritative = authoritative
        self._lock_stack: list[tuple[int, str]] = []
        self._closed = False

    def __enter__(self) -> "_SecureRootSession":
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        if self._lock_stack:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        self._closed = True
        closer = _win_close if os.name == "nt" else os.close
        for handle in reversed(self._handles):
            closer(handle)
        self._handles.clear()

    def _require_open(self) -> None:
        if self._closed:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")

    def _holds_global_lock(self) -> bool:
        self._require_open()
        return any(rank == 0 for rank, _ in self._lock_stack)

    @property
    def _root_handle(self) -> int:
        self._require_open()
        return self._directories[()]

    @contextlib.contextmanager
    def _directory_chain(
        self,
        components: tuple[str, ...],
        *,
        final_exact_private: bool | None = None,
    ) -> Iterator[tuple[int, str]]:
        self._require_open()
        self._verify_root_binding()
        components = _relative_components(components)
        current = self._root_handle
        current_path = self._root_path
        try:
            prefix: tuple[str, ...] = ()
            for component in components:
                prefix += (component,)
                current_path = os.path.join(current_path, component)
                child = self._directories.get(prefix)
                profile = self._directory_profiles.get(prefix)
                if profile is None:
                    profile = (
                        "confidential"
                        if prefix != components or final_exact_private is not False
                        else "integrity_only"
                    )
                if child is None:
                    try:
                        if os.name == "nt":
                            child = _win_open_path(current_path, directory=True)
                            _verify_win_object(child, current_path, directory=True)
                            self._verify_windows_profile(
                                child, directory=True, profile=profile
                            )
                        else:
                            flags = (
                                os.O_RDONLY
                                | getattr(os, "O_CLOEXEC", 0)
                                | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0)
                            )
                            child = os.open(component, flags, dir_fd=current)
                            _verify_posix_directory(
                                child,
                                exact_private=self._exact_private,
                                expected_uid=self._expected_uid,
                            )
                    except BaseException:
                        if child is not None:
                            (_win_close if os.name == "nt" else os.close)(child)
                        raise
                    self._directories[prefix] = child
                    self._directory_profiles[prefix] = profile
                    self._handles.append(child)
                elif os.name == "nt":
                    _verify_win_object(child, current_path, directory=True)
                    self._verify_windows_profile(
                        child, directory=True, profile=profile
                    )
                else:
                    _verify_posix_directory(
                        child,
                        exact_private=self._exact_private,
                        expected_uid=self._expected_uid,
                    )
                    flags = (
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    rebound = os.open(component, flags, dir_fd=current)
                    try:
                        if _posix_identity(rebound) != _posix_identity(child):
                            raise BirthSecureFSError(
                                "birth_provisioning_recovery_ambiguous"
                            )
                    finally:
                        os.close(rebound)
                current = child
            yield current, current_path
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc

    def _verify_windows_profile(
        self,
        handle: int,
        *,
        directory: bool,
        profile: Literal["confidential", "integrity_only"],
    ) -> None:
        if os.name != "nt" or not self._authoritative:
            return
        if self._service_sid is None:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        with _win_security_attributes(
            profile, directory=directory, service_sid=self._service_sid
        ) as (_, descriptor):
            _win_verify_security(handle, descriptor)

    def _verify_root_binding(self) -> None:
        if os.name != "posix" or self._root_parent_handle is None or not self._root_name:
            return
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            rebound = os.open(
                self._root_name, flags, dir_fd=self._root_parent_handle
            )
            try:
                if _posix_identity(rebound) != _posix_identity(self._root_handle):
                    raise BirthSecureFSError(
                        "birth_provisioning_recovery_ambiguous"
                    )
            finally:
                os.close(rebound)
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError(
                "birth_provisioning_recovery_ambiguous"
            ) from exc

    def open_directory(
        self, components: tuple[str, ...], *, exact_private: bool | None = None
    ) -> _SecureDirectoryHandle:
        components = _relative_components(components)
        with self._directory_chain(
            components, final_exact_private=exact_private
        ) as (handle, expected):
            if os.name == "nt":
                _verify_win_object(handle, expected, directory=True)
            else:
                _verify_posix_directory(
                    handle,
                    exact_private=self._exact_private if exact_private is None else exact_private,
                    expected_uid=self._expected_uid,
                )
        return _SecureDirectoryHandle(self, components)

    def read_file(
        self,
        components: tuple[str, ...],
        *,
        maximum: int,
        exact_private: bool = True,
    ) -> bytes:
        components = _relative_components(components)
        if not components or isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        parent, name = components[:-1], components[-1]
        with self._directory_chain(parent) as (directory, directory_path):
            if os.name == "nt":
                return self._read_file_windows(
                    components, directory_path, name, maximum, exact_private
                )
            return self._read_file_posix(directory, name, maximum, exact_private)

    def _read_file_posix(
        self,
        directory: int,
        name: str,
        maximum: int,
        exact_private: bool,
    ) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=directory)
            try:
                _verify_posix_file(
                    fd,
                    exact_private=exact_private,
                    expected_uid=self._expected_uid,
                )
                before = _posix_snapshot(fd)
                if before[5] > maximum:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable")
                result = bytearray()
                while len(result) <= maximum:
                    block = os.read(fd, min(8192, maximum + 1 - len(result)))
                    if not block:
                        break
                    result.extend(block)
                after = _posix_snapshot(fd)
                if len(result) > maximum or before != after or len(result) != before[5]:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable")
                return bytes(result)
            finally:
                os.close(fd)
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc

    def _read_file_windows(
        self,
        components: tuple[str, ...],
        directory_path: str,
        name: str,
        maximum: int,
        exact_private: bool,
    ) -> bytes:
        path = os.path.join(directory_path, name)
        handle = None
        try:
            handle = _win_open_path(path, directory=False)
            before = _verify_win_object(handle, path, directory=False)
            profile = "confidential" if exact_private else "integrity_only"
            self._verify_windows_profile(
                handle, directory=False, profile=profile
            )
            self._file_profiles.setdefault(components, profile)
            if self._file_profiles[components] != profile:
                raise BirthSecureFSError("birth_provisioning_acl_unsafe")
            size = before[5]
            if size > maximum:
                raise BirthSecureFSError("birth_provisioning_io_unavailable")
            result = bytearray()
            while len(result) <= maximum:
                capacity = min(8192, maximum + 1 - len(result))
                buffer = ctypes.create_string_buffer(capacity)
                count = wintypes.DWORD()
                if not _KERNEL32.ReadFile(
                    handle, buffer, capacity, ctypes.byref(count), None
                ):
                    raise _win_error("ReadFile")
                if not count.value:
                    break
                result.extend(buffer.raw[: count.value])
            after = _verify_win_object(handle, path, directory=False)
            if len(result) > maximum or len(result) != size or before != after:
                raise BirthSecureFSError("birth_provisioning_io_unavailable")
            return bytes(result)
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        finally:
            if handle is not None:
                _win_close(handle)

    def inventory(self, components: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item.name for item in self._inventory_state(components))

    def _inventory_state(
        self, components: tuple[str, ...]
    ) -> tuple[_InventoryEntry, ...]:
        components = _relative_components(components)
        with self._directory_chain(components) as (handle, _):
            try:
                before = (
                    _win_inventory(handle)
                    if os.name == "nt"
                    else _posix_inventory(handle)
                )
                after = (
                    _win_inventory(handle)
                    if os.name == "nt"
                    else _posix_inventory(handle)
                )
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
            if before != after:
                raise BirthSecureFSError("birth_provisioning_io_unavailable")
            if len({item.name for item in before}) != len(before):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            return before

    @contextlib.contextmanager
    def global_lock(
        self,
        *,
        exclusive: bool,
        create: bool,
        timeout: float = 5.0,
    ) -> Iterator[None]:
        if not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        with self._lock_file(
            ("provisioning-v1.lock",),
            exclusive=exclusive,
            create=create,
            timeout=timeout,
            rank=0,
            order_key="provisioning-v1.lock",
        ):
            yield

    @contextlib.contextmanager
    def local_lock(
        self,
        directory: tuple[str, ...],
        *,
        exclusive: bool = False,
        create: bool = False,
        timeout: float = 5.0,
    ) -> Iterator[None]:
        directory = _relative_components(directory)
        if (exclusive or create) and not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        with self._lock_file(
            directory + ("birth-keystore.lock",),
            exclusive=exclusive,
            create=create,
            timeout=timeout,
            rank=1,
            order_key="/".join(directory) or ".",
        ):
            yield

    @contextlib.contextmanager
    def _lock_file(
        self,
        components: tuple[str, ...],
        *,
        exclusive: bool,
        create: bool,
        timeout: float = 5.0,
        rank: int,
        order_key: str,
    ) -> Iterator[None]:
        components = _relative_components(components)
        if (
            not components
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise BirthSecureFSError("birth_provisioning_lock_unavailable")
        if rank not in {0, 1}:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        key = order_key
        if not isinstance(key, str) or not key:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        if self._lock_stack:
            previous_rank, previous_key = self._lock_stack[-1]
            if rank < previous_rank or (
                rank == previous_rank == 1 and key <= previous_key
            ):
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        if rank == 0 and any(item_rank == 0 for item_rank, _ in self._lock_stack):
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        parent, name = components[:-1], components[-1]
        with self._directory_chain(parent) as (directory, directory_path):
            if os.name == "nt":
                with self._win_lock(directory_path, name, exclusive, create, timeout):
                    self._lock_stack.append((rank, key))
                    try:
                        yield
                    finally:
                        if self._lock_stack.pop() != (rank, key):
                            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            else:
                with self._posix_lock(directory, name, exclusive, create, timeout):
                    self._lock_stack.append((rank, key))
                    try:
                        yield
                    finally:
                        if self._lock_stack.pop() != (rank, key):
                            raise BirthSecureFSError("birth_provisioning_lock_unsafe")

    @contextlib.contextmanager
    def _posix_lock(
        self,
        directory: int,
        name: str,
        exclusive: bool,
        create: bool,
        timeout: float,
    ) -> Iterator[None]:
        import fcntl

        flags = (os.O_RDWR if exclusive else os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = None
        created = False
        try:
            if create and exclusive:
                try:
                    fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
                    created = True
                except FileExistsError:
                    fd = os.open(name, flags, dir_fd=directory)
            else:
                fd = os.open(name, flags, dir_fd=directory)
            _verify_posix_file(
                fd, exact_private=True, expected_uid=self._expected_uid
            )
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            deadline = time.monotonic() + timeout
            delay_index = 0
            while True:
                try:
                    fcntl.flock(fd, operation | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise BirthSecureFSError("birth_provisioning_lock_unsafe") from exc
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BirthSecureFSError("birth_provisioning_lock_unavailable") from exc
                    delay = _LOCK_DELAYS[min(delay_index, len(_LOCK_DELAYS) - 1)]
                    delay_index += 1
                    time.sleep(min(delay, remaining))
            before = _posix_snapshot(fd)
            if before[5] == 0 and exclusive:
                _write_all_posix(fd, _LOCK_BYTE)
                os.fsync(fd)
                if created:
                    os.fsync(directory)
            elif before[5] != 1:
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, 2) != _LOCK_BYTE:
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            yield
        except FileNotFoundError as exc:
            raise BirthSecureFSError("birth_provisioning_lock_unavailable") from exc
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe") from exc
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    @contextlib.contextmanager
    def _win_lock(
        self,
        directory_path: str,
        name: str,
        exclusive: bool,
        create: bool,
        timeout: float,
    ) -> Iterator[None]:
        path = os.path.join(directory_path, name)
        handle = None
        locked = False
        overlapped = _OVERLAPPED()
        try:
            try:
                if create and exclusive:
                    if self._service_sid is None:
                        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
                    _win_require_supported_volume(self._root_handle)
                    with _win_restore_privilege():
                        with _win_security_attributes(
                            "integrity_only",
                            directory=False,
                            service_sid=self._service_sid,
                        ) as (attributes, descriptor):
                            handle = _win_open_path(
                                path,
                                directory=False,
                                writable=True,
                                create=True,
                                security_attributes=ctypes.byref(attributes),
                                security_write=True,
                            )
                            _win_apply_and_verify_security(handle, descriptor)
                else:
                    handle = _win_open_path(
                        path,
                        directory=False,
                        writable=exclusive,
                        generic_read=not exclusive,
                    )
            except OSError as exc:
                if create and exclusive and exc.errno in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                    handle = _win_open_path(
                        path, directory=False, writable=True, generic_read=True
                    )
                else:
                    raise
            deadline = time.monotonic() + timeout
            delay_index = 0
            flags = _LOCKFILE_FAIL_IMMEDIATELY
            if exclusive:
                flags |= _LOCKFILE_EXCLUSIVE_LOCK
            while True:
                if _KERNEL32.LockFileEx(
                    handle, flags, 0, 1, 0, ctypes.byref(overlapped)
                ):
                    locked = True
                    break
                code = ctypes.get_last_error()
                if code != _ERROR_LOCK_VIOLATION:
                    raise OSError(code, "LockFileEx")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BirthSecureFSError("birth_provisioning_lock_unavailable")
                delay = _LOCK_DELAYS[min(delay_index, len(_LOCK_DELAYS) - 1)]
                delay_index += 1
                time.sleep(min(delay, remaining))
            before = _verify_win_object(handle, path, directory=False)
            if self._service_sid is not None:
                with _win_security_attributes(
                    "integrity_only", directory=False, service_sid=self._service_sid
                ) as (_, descriptor):
                    _win_verify_security(handle, descriptor)
            size = before[5]
            if size == 0 and exclusive:
                _win_write_all(handle, _LOCK_BYTE)
                if not _KERNEL32.FlushFileBuffers(handle):
                    raise _win_error("FlushFileBuffers")
            elif size != 1:
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            if not _KERNEL32.SetFilePointerEx(handle, 0, None, 0):
                raise _win_error("SetFilePointerEx")
            buffer = ctypes.create_string_buffer(2)
            count = wintypes.DWORD()
            if not _KERNEL32.ReadFile(handle, buffer, 2, ctypes.byref(count), None):
                raise _win_error("ReadFile")
            if count.value != 1 or buffer.raw[:1] != _LOCK_BYTE:
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            yield
        except BirthSecureFSError:
            raise
        except OSError as exc:
            code = "birth_provisioning_lock_unavailable" if exc.errno in {
                _ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND
            } else "birth_provisioning_lock_unsafe"
            raise BirthSecureFSError(code) from exc
        finally:
            if locked and not _KERNEL32.UnlockFileEx(
                handle, 0, 1, 0, ctypes.byref(overlapped)
            ):
                unlock_error = _win_error("UnlockFileEx")
                raise BirthSecureFSError("birth_provisioning_lock_unsafe") from unlock_error
            if handle is not None:
                _win_close(handle)

    def create_file_exclusive(
        self,
        components: tuple[str, ...],
        payload: bytes,
        *,
        profile: Literal["confidential", "integrity_only"],
    ) -> _ObjectIdentity:
        if not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        components = _relative_components(components)
        if not components or not isinstance(payload, bytes) or profile not in {
            "confidential", "integrity_only"
        }:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        parent, name = components[:-1], components[-1]
        with self._directory_chain(parent) as (directory, directory_path):
            if os.name == "nt":
                return self._create_file_exclusive_windows(
                    directory_path, name, payload, profile
                )
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                fd = os.open(name, flags, 0o600, dir_fd=directory)
                try:
                    _verify_posix_file(
                        fd, exact_private=True, expected_uid=self._expected_uid
                    )
                    before = _posix_identity(fd)
                    _write_all_posix(fd, payload)
                    os.fsync(fd)
                    os.lseek(fd, 0, os.SEEK_SET)
                    if _read_all_posix(fd, len(payload)) != payload:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                    if _posix_identity(fd) != before:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                    os.fsync(directory)
                    return before
                finally:
                    os.close(fd)
            except FileExistsError as exc:
                raise BirthSecureFSError("birth_provisioning_transaction_conflict") from exc
            except BirthSecureFSError:
                raise
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc

    def _create_file_exclusive_windows(
        self,
        directory_path: str,
        name: str,
        payload: bytes,
        profile: Literal["confidential", "integrity_only"],
    ) -> _ObjectIdentity:
        if self._service_sid is None:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        _win_require_supported_volume(self._root_handle)
        path = os.path.join(directory_path, name)
        handle = None
        created = False
        complete = False
        try:
            with _win_restore_privilege():
                with _win_security_attributes(
                    profile, directory=False, service_sid=self._service_sid
                ) as (attributes, descriptor):
                    handle = _win_open_path(
                        path,
                        directory=False,
                        writable=True,
                        delete=True,
                        create=True,
                        security_attributes=ctypes.byref(attributes),
                        security_write=True,
                    )
                    created = True
                    before = _verify_win_object(handle, path, directory=False)
                    _win_apply_and_verify_security(handle, descriptor)
                    _win_write_all(handle, payload)
                    if not _KERNEL32.FlushFileBuffers(handle):
                        raise _win_error("FlushFileBuffers")
                    if not _KERNEL32.SetFilePointerEx(handle, 0, None, 0):
                        raise _win_error("SetFilePointerEx")
                    actual = bytearray()
                    while len(actual) <= len(payload):
                        capacity = min(8192, len(payload) + 1 - len(actual))
                        buffer = ctypes.create_string_buffer(capacity)
                        count = wintypes.DWORD()
                        if not _KERNEL32.ReadFile(
                            handle, buffer, capacity, ctypes.byref(count), None
                        ):
                            raise _win_error("ReadFile")
                        if not count.value:
                            break
                        actual.extend(buffer.raw[: count.value])
                    after = _verify_win_object(handle, path, directory=False)
                    if bytes(actual) != payload or before[0] != after[0]:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                    complete = True
                    return before[0]
        except OSError as exc:
            if exc.errno in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                raise BirthSecureFSError("birth_provisioning_transaction_conflict") from exc
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        except BirthSecureFSError:
            raise
        finally:
            if handle is not None:
                if created and not complete:
                    try:
                        _win_dispose_created(handle)
                    except OSError:
                        pass
                _win_close(handle)

    def create_directory_exclusive(
        self,
        components: tuple[str, ...],
        *,
        profile: Literal["confidential", "integrity_only"],
    ) -> _SecureDirectoryHandle:
        if not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        components = _relative_components(components)
        if not components or profile not in {"confidential", "integrity_only"}:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        parent, name = components[:-1], components[-1]
        with self._directory_chain(parent) as (directory, directory_path):
            if os.name == "nt":
                handle = self._create_directory_exclusive_windows(
                    directory_path,
                    name,
                    profile,
                )
                self._directories[components] = handle
                self._handles.append(handle)
                return _SecureDirectoryHandle(self, components)
            try:
                os.mkdir(name, 0o700, dir_fd=directory)
                os.fsync(directory)
            except FileExistsError as exc:
                raise BirthSecureFSError("birth_provisioning_transaction_conflict") from exc
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        return self.open_directory(components, exact_private=True)

    def _create_directory_exclusive_windows(
        self,
        directory_path: str,
        name: str,
        profile: Literal["confidential", "integrity_only"],
    ) -> int:
        if self._service_sid is None:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        _win_require_supported_volume(self._root_handle)
        path = os.path.join(directory_path, name)
        handle = None
        created = False
        complete = False
        try:
            with _win_restore_privilege():
                with _win_security_attributes(
                    profile, directory=True, service_sid=self._service_sid
                ) as (attributes, descriptor):
                    if not _KERNEL32.CreateDirectoryW(path, ctypes.byref(attributes)):
                        raise _win_error("CreateDirectoryW")
                    created = True
                    handle = _win_open_path(
                        path,
                        directory=True,
                        delete=True,
                        security_write=True,
                    )
                    _verify_win_object(handle, path, directory=True)
                    _win_apply_and_verify_security(handle, descriptor)
                    complete = True
                    return handle
        except OSError as exc:
            if exc.errno in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                raise BirthSecureFSError("birth_provisioning_transaction_conflict") from exc
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        except BirthSecureFSError:
            raise
        finally:
            if handle is not None and not complete:
                if created and not complete:
                    try:
                        _win_dispose_created(handle)
                    except OSError:
                        pass
                _win_close(handle)

    def rename_no_replace(
        self, source: tuple[str, ...], destination: tuple[str, ...], *, directory: bool
    ) -> _ObjectIdentity:
        if not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        source = _relative_components(source)
        destination = _relative_components(destination)
        if not source or not destination:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        if destination in self._directories:
            raise BirthSecureFSError("birth_provisioning_transaction_conflict")
        if os.name == "nt":
            return self._rename_no_replace_windows(source, destination, directory)
        return self._rename_no_replace_posix(source, destination, directory)

    def _rename_no_replace_posix(
        self, source: tuple[str, ...], destination: tuple[str, ...], directory: bool
    ) -> _ObjectIdentity:
        source_parent, source_name = source[:-1], source[-1]
        target_parent, target_name = destination[:-1], destination[-1]
        with self._directory_chain(source_parent) as (source_fd, _):
            with self._directory_chain(target_parent) as (target_fd, _):
                flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                if directory:
                    flags |= getattr(os, "O_DIRECTORY", 0)
                try:
                    object_fd = os.open(source_name, flags, dir_fd=source_fd)
                    try:
                        if directory:
                            _verify_posix_directory(
                                object_fd,
                                exact_private=True,
                                expected_uid=self._expected_uid,
                            )
                        else:
                            _verify_posix_file(
                                object_fd,
                                exact_private=True,
                                expected_uid=self._expected_uid,
                            )
                        identity = _posix_identity(object_fd)
                        if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev:
                            raise BirthSecureFSError(
                                "birth_provisioning_atomic_install_unsupported"
                            )
                        _renameat2_no_replace(source_fd, source_name, target_fd, target_name)
                        os.fsync(source_fd)
                        if source_fd != target_fd:
                            os.fsync(target_fd)
                    finally:
                        os.close(object_fd)
                except BirthSecureFSError:
                    raise
                except OSError as exc:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        with self._directory_chain(target_parent) as (target_fd, _):
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            if directory:
                flags |= getattr(os, "O_DIRECTORY", 0)
            try:
                final_fd = os.open(target_name, flags, dir_fd=target_fd)
                try:
                    if directory:
                        _verify_posix_directory(
                            final_fd,
                            exact_private=True,
                            expected_uid=self._expected_uid,
                        )
                    else:
                        _verify_posix_file(
                            final_fd,
                            exact_private=True,
                            expected_uid=self._expected_uid,
                        )
                    if _posix_identity(final_fd) != identity:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                finally:
                    os.close(final_fd)
            except BirthSecureFSError:
                raise
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        self._remap_cached_directories(source, destination)
        return identity

    def _rename_no_replace_windows(
        self, source: tuple[str, ...], destination: tuple[str, ...], directory: bool
    ) -> _ObjectIdentity:
        source_parent, source_name = source[:-1], source[-1]
        target_parent, target_name = destination[:-1], destination[-1]
        with self._directory_chain(source_parent) as (_, source_path):
            with self._directory_chain(target_parent) as (target_handle, target_path):
                source_path = os.path.join(source_path, source_name)
                source_handle = self._directories.get(source) if directory else None
                close_source = source_handle is None
                try:
                    if source_handle is None:
                        source_handle = _win_open_path(
                            source_path, directory=directory, delete=True
                        )
                    before = _verify_win_object(source_handle, source_path, directory=directory)
                    target_identity = _win_info(target_handle)[0]
                    if before[0].volume != target_identity.volume:
                        raise BirthSecureFSError(
                            "birth_provisioning_atomic_install_unsupported"
                        )
                    encoded = target_name.encode("utf-16-le")
                    offset = _FILE_RENAME_INFO_HEADER.FileName.offset
                    buffer = ctypes.create_string_buffer(offset + len(encoded))
                    header = _FILE_RENAME_INFO_HEADER.from_buffer(buffer)
                    header.ReplaceIfExists = False
                    header.RootDirectory = target_handle
                    header.FileNameLength = len(encoded)
                    ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
                    if not _KERNEL32.SetFileInformationByHandle(
                        source_handle,
                        _FILE_RENAME_INFO_CLASS,
                        buffer,
                        len(buffer),
                    ):
                        error = ctypes.get_last_error()
                        if error in {
                            _ERROR_FILE_EXISTS,
                            _ERROR_ALREADY_EXISTS,
                            _ERROR_ACCESS_DENIED,
                            _ERROR_SHARING_VIOLATION,
                        } and _win_destination_exists(target_path, target_name, directory):
                            raise BirthSecureFSError(
                                "birth_provisioning_transaction_conflict"
                            )
                        if error in {_ERROR_NOT_SUPPORTED, _ERROR_NOT_SAME_DEVICE}:
                            raise BirthSecureFSError(
                                "birth_provisioning_atomic_install_unsupported"
                            )
                        raise OSError(error, "SetFileInformationByHandle")
                    after = _win_info(source_handle)
                    if after[0] != before[0]:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                    identity = before[0]
                except BirthSecureFSError:
                    raise
                except OSError as exc:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
                finally:
                    if source_handle is not None and close_source:
                        _win_close(source_handle)
        with self._directory_chain(target_parent) as (_, target_path):
            final_path = os.path.join(target_path, target_name)
            final_handle = None
            try:
                final_handle = _win_open_path(final_path, directory=directory)
                if _verify_win_object(final_handle, final_path, directory=directory)[0] != identity:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable")
            except BirthSecureFSError:
                raise
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
            finally:
                if final_handle is not None:
                    _win_close(final_handle)
        self._remap_cached_directories(source, destination)
        return identity

    def _remap_cached_directories(
        self, source: tuple[str, ...], destination: tuple[str, ...]
    ) -> None:
        moved = {
            key: destination + key[len(source) :]
            for key in tuple(self._directories)
            if key[: len(source)] == source
        }
        if any(target in self._directories and target not in moved for target in moved.values()):
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        for key, target in moved.items():
            self._directories[target] = self._directories.pop(key)


class _LegacyReadSession:
    """Path compatibility facade with no provisioning or global-lock capability."""

    __slots__ = ("_session",)

    def __init__(self, token: object, session: _SecureRootSession) -> None:
        if token is not _LEGACY_TOKEN:
            raise TypeError("private legacy session")
        self._session = session

    def __enter__(self) -> "_LegacyReadSession":
        self._session._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def open_directory(
        self, components: tuple[str, ...], *, exact_private: bool | None = None
    ) -> _SecureDirectoryHandle:
        return self._session.open_directory(
            components, exact_private=exact_private
        )

    def read_file(
        self,
        components: tuple[str, ...],
        *,
        maximum: int,
        exact_private: bool = True,
    ) -> bytes:
        return self._session.read_file(
            components, maximum=maximum, exact_private=exact_private
        )

    def inventory(self, components: tuple[str, ...]) -> tuple[str, ...]:
        return self._session.inventory(components)

    def _inventory_state(
        self, components: tuple[str, ...]
    ) -> tuple[_InventoryEntry, ...]:
        return self._session._inventory_state(components)

    @contextlib.contextmanager
    def local_lock(
        self,
        directory: tuple[str, ...],
        *,
        exclusive: bool = False,
        create: bool = False,
        timeout: float = 5.0,
    ) -> Iterator[None]:
        if exclusive or create:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        with self._session.local_lock(
            directory,
            exclusive=False,
            create=False,
            timeout=timeout,
        ):
            yield


_DESCRIPTOR_TOKEN = object()
_SESSION_TOKEN = object()
_LEGACY_TOKEN = object()


def _open_legacy_root_session(
    root: Path, *, exact_private: bool = True
) -> _LegacyReadSession:
    """Open one historical Path root through a read-only compatibility facade."""
    if not isinstance(root, Path):
        root = Path(root)
    if os.name == "nt":
        handles, absolute = _open_win_root(root)
        expected_uid = None
    else:
        expected_uid = os.geteuid() if exact_private else None
        handles, absolute = _open_posix_root(
            root, exact_private=exact_private, expected_uid=expected_uid
        )
    session = _SecureRootSession(
        _SESSION_TOKEN,
        handles,
        absolute,
        exact_private=exact_private,
        service_sid=None,
        expected_uid=expected_uid,
        authoritative=False,
    )
    return _LegacyReadSession(_LEGACY_TOKEN, session)


def _adopt_authenticated_root(
    descriptor: _AuthenticatedRootDescriptor,
) -> _SecureRootSession:
    """Consume an installer-authenticated descriptor without accepting path policy."""
    if not isinstance(descriptor, _AuthenticatedRootDescriptor) or descriptor._adopted:
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    identity = descriptor.identity
    if os.name == "nt":
        if identity.windows_service_sid is None or identity.posix_uid is not None:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    elif identity.posix_uid is None or identity.windows_service_sid is not None:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    descriptor._adopted = True
    handles = descriptor.handles
    descriptor.handles = []
    return _SecureRootSession(
        _SESSION_TOKEN,
        handles,
        descriptor.root_path,
        exact_private=True,
        service_sid=identity.windows_service_sid,
        expected_uid=identity.posix_uid,
        authoritative=True,
    )


def _read_path_once(
    path: Path, *, maximum: int, exact_private: bool
) -> bytes:
    path = Path(path)
    with _open_legacy_root_session(
        path.parent, exact_private=exact_private
    ) as session:
        return session.read_file(
            (path.name,), maximum=maximum, exact_private=exact_private
        )


def _write_all_posix(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        try:
            count = os.write(fd, view[written:])
        except InterruptedError:
            continue
        if count <= 0:
            raise OSError(errno.EIO, "short write")
        written += count


def _read_all_posix(fd: int, size: int) -> bytes:
    result = bytearray()
    while len(result) <= size:
        block = os.read(fd, min(8192, size + 1 - len(result)))
        if not block:
            break
        result.extend(block)
    return bytes(result)


def _posix_inventory(directory: int) -> tuple[_InventoryEntry, ...]:
    result: list[_InventoryEntry] = []
    names = tuple(os.listdir(directory))
    flags = (
        getattr(os, "O_PATH", os.O_RDONLY)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for raw_name in names:
        name = _relative_components((raw_name,))[0]
        handle = os.open(name, flags, dir_fd=directory)
        try:
            value = os.fstat(handle)
            if (
                not stat.S_ISLNK(value.st_mode)
                and not stat.S_ISDIR(value.st_mode)
                and not stat.S_ISREG(value.st_mode)
            ):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            result.append(
                _InventoryEntry(
                    name,
                    _ObjectIdentity(f"{value.st_dev:x}", f"{value.st_ino:x}"),
                    stat.S_ISDIR(value.st_mode),
                    value.st_nlink,
                )
            )
        finally:
            os.close(handle)
    return tuple(sorted(result, key=lambda item: item.name.encode("utf-8")))


def _renameat2_no_replace(
    source_fd: int, source_name: str, target_fd: int, target_name: str
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise BirthSecureFSError("birth_provisioning_atomic_install_unsupported")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_fd,
        os.fsencode(source_name),
        target_fd,
        os.fsencode(target_name),
        1,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise BirthSecureFSError("birth_provisioning_transaction_conflict")
    if error in {
        errno.EXDEV,
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise BirthSecureFSError("birth_provisioning_atomic_install_unsupported")
    raise OSError(error, "renameat2")


def _win_write_all(handle: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        block = payload[written : written + 1024 * 1024]
        buffer = ctypes.create_string_buffer(block)
        count = wintypes.DWORD()
        if not _KERNEL32.WriteFile(
            handle, buffer, len(block), ctypes.byref(count), None
        ):
            raise _win_error("WriteFile")
        if count.value <= 0:
            raise OSError(errno.EIO, "WriteFile")
        written += count.value


def _win_destination_exists(parent_path: str, name: str, directory: bool) -> bool:
    handle = None
    try:
        handle = _win_open_path(os.path.join(parent_path, name), directory=directory)
        return True
    except OSError as exc:
        if exc.errno in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            return False
        raise
    finally:
        if handle is not None:
            _win_close(handle)


def _win_inventory(handle: int) -> tuple[_InventoryEntry, ...]:
    result: list[_InventoryEntry] = []
    volume = _win_info(handle)[0].volume
    first = True
    buffer = ctypes.create_string_buffer(64 * 1024)
    while True:
        info_class = (
            _FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS
            if first
            else _FILE_ID_EXTD_DIRECTORY_INFO_CLASS
        )
        first = False
        if not _KERNEL32.GetFileInformationByHandleEx(
            handle, info_class, buffer, len(buffer)
        ):
            error = ctypes.get_last_error()
            if error == _ERROR_NO_MORE_FILES:
                break
            raise OSError(error, "GetFileInformationByHandleEx(directory)")
        offset = 0
        while True:
            entry = _FILE_ID_EXTD_DIR_INFO.from_buffer(buffer, offset)
            name_offset = offset + _FILE_ID_EXTD_DIR_INFO.FileName.offset
            if entry.FileNameLength % 2 or name_offset + entry.FileNameLength > len(buffer):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            name = ctypes.wstring_at(
                ctypes.addressof(buffer) + name_offset, entry.FileNameLength // 2
            )
            if name not in {".", ".."}:
                name = _relative_components((name,))[0]
                result.append(
                    _InventoryEntry(
                        name,
                        _ObjectIdentity(volume, bytes(entry.FileId.Identifier).hex()),
                        bool(entry.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY),
                        1,
                    )
                )
            if entry.NextEntryOffset == 0:
                break
            if entry.NextEntryOffset < _FILE_ID_EXTD_DIR_INFO.FileName.offset:
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            offset += entry.NextEntryOffset
            if offset >= len(buffer):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
    return tuple(sorted(result, key=lambda item: item.name.encode("utf-8")))
