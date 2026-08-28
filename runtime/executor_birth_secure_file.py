"""Low-level immutable regular-file reader for executor Birth authorities.

The module has no semantic, signing, publication or ownership dependency.  It
provides the single descriptor/handle oracle shared by higher Birth layers.
"""
from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path


class SecureFileReadError(RuntimeError):
    """The file could not be observed once as a bounded immutable regular file."""


if os.name == "nt":
    from ctypes import wintypes

    class _WinFileInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD), ("creation_low", wintypes.DWORD),
            ("creation_high", wintypes.DWORD), ("access_low", wintypes.DWORD),
            ("access_high", wintypes.DWORD), ("write_low", wintypes.DWORD),
            ("write_high", wintypes.DWORD), ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD), ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    class _WinFileStandardInfo(ctypes.Structure):
        _fields_ = [
            ("allocation_size", ctypes.c_longlong),
            ("end_of_file", ctypes.c_longlong),
            ("links", wintypes.DWORD),
            ("delete_pending", ctypes.c_ubyte),
            ("directory", ctypes.c_ubyte),
        ]

    class _WinFileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(_WinFileInfo),
    )
    _KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    )
    _KERNEL32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _KERNEL32.GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
    )
    _KERNEL32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _KERNEL32.ReadFile.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    )
    _KERNEL32.ReadFile.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _KERNEL32.CloseHandle.restype = wintypes.BOOL


def _win_error(operation: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), operation)


def _win_open(path: Path, *, directory: bool = False) -> int:
    # Sharing reads only denies writers, renames and deletion until the stable
    # observation is complete. OPEN_REPARSE_POINT prevents transparent links.
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    access = 0x00000001 if directory else 0x80000000
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    handle = _KERNEL32.CreateFileW(
        str(path), access, 0x00000001, None, 3, flags, None,
    )
    if handle in {None, ctypes.c_void_p(-1).value}:
        raise _win_error("CreateFileW")
    return handle


def _win_info(handle: int) -> tuple[int, ...]:
    info = _WinFileInfo()
    if not _KERNEL32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise _win_error("GetFileInformationByHandle")
    return (
        info.attributes, info.write_high, info.write_low, info.volume,
        info.size_high, info.size_low, info.links, info.file_index_high,
        info.file_index_low,
    )


def _win_file_shape(handle: int) -> tuple[int, int, int, bool, bool]:
    standard = _WinFileStandardInfo()
    if not _KERNEL32.GetFileInformationByHandleEx(
        handle, 1, ctypes.byref(standard), ctypes.sizeof(standard),
    ):
        raise _win_error("GetFileInformationByHandleEx(FileStandardInfo)")
    tagged = _WinFileAttributeTagInfo()
    if not _KERNEL32.GetFileInformationByHandleEx(
        handle, 9, ctypes.byref(tagged), ctypes.sizeof(tagged),
    ):
        raise _win_error("GetFileInformationByHandleEx(FileAttributeTagInfo)")
    return (
        int(tagged.attributes), int(standard.end_of_file), int(standard.links),
        bool(standard.delete_pending), bool(standard.directory),
    )


def _win_final_path(handle: int) -> str:
    needed = _KERNEL32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not needed:
        raise _win_error("GetFinalPathNameByHandleW")
    buffer = ctypes.create_unicode_buffer(needed + 1)
    written = _KERNEL32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not written or written >= len(buffer):
        raise _win_error("GetFinalPathNameByHandleW")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def _win_read(handle: int, maximum: int) -> bytes:
    result = bytearray()
    while len(result) <= maximum:
        capacity = min(8192, maximum + 1 - len(result))
        buffer = ctypes.create_string_buffer(capacity)
        count = wintypes.DWORD()
        if not _KERNEL32.ReadFile(
            handle, buffer, capacity, ctypes.byref(count), None,
        ):
            raise _win_error("ReadFile")
        if not count.value:
            break
        result.extend(buffer.raw[:count.value])
    return bytes(result)


def _win_close(handle: int) -> None:
    _KERNEL32.CloseHandle(handle)


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
        before.st_size, before.st_mtime_ns, before.st_ctime_ns,
    ) == (
        after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    )


def read_immutable_regular_file(path: Path, *, maximum: int) -> bytes:
    """Return one stable bounded file observation or fail closed."""
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        raise SecureFileReadError("invalid byte limit")
    if os.name == "nt":
        handle = None
        try:
            handle = _win_open(path, directory=False)
            before = _win_info(handle)
            shape_before = _win_file_shape(handle)
            attributes, size, links, delete_pending, directory = shape_before
            if (
                attributes & 0x00000400 or directory or delete_pending
                or links != 1 or size < 0 or size > maximum
            ):
                raise ValueError("unsafe file")
            if _win_final_path(handle) != os.path.normcase(os.path.abspath(path)):
                raise ValueError("unexpected final path")
            raw = _win_read(handle, maximum)
            if (
                len(raw) > maximum or len(raw) != size
                or _win_info(handle) != before
                or _win_file_shape(handle) != shape_before
            ):
                raise ValueError("file changed")
            return raw
        except (OSError, ValueError) as exc:
            raise SecureFileReadError(path.name) from exc
        finally:
            if handle is not None:
                _win_close(handle)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > maximum or before.st_mode & 0o022
            ):
                raise ValueError("unsafe file")
            raw = bytearray()
            while len(raw) <= maximum:
                block = os.read(
                    descriptor, min(8192, maximum + 1 - len(raw)),
                )
                if not block:
                    break
                raw.extend(block)
            after = os.fstat(descriptor)
            if len(raw) > maximum or not _same_file(before, after):
                raise ValueError("file changed")
            return bytes(raw)
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as exc:
        raise SecureFileReadError(path.name) from exc
