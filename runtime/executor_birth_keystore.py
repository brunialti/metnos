"""Load-only, dedicated Ed25519 key store for Executor Birth.

Provisioning and rotation deliberately live outside the runtime.  This module
only opens a pre-existing, canonically described store while holding its lock;
it never creates keys, repairs files, or falls back to the general author key.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Iterable, Iterator, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


CONFIG_BASENAME = "keystore.json"
LOCK_BASENAME = "birth-keystore.lock"
SCHEMA_VERSION = 1
KEY_ID_VERSION = 1
_KEY_ID_RE = re.compile(r"birth-ed25519-v1-sha256-([0-9a-f]{64})\Z")


if os.name == "nt":  # pragma: no cover - exercised by Windows CI
    import ctypes
    from ctypes import wintypes

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("ace_count", wintypes.DWORD),
            ("acl_bytes_in_use", wintypes.DWORD),
            ("acl_bytes_free", wintypes.DWORD),
        ]

    class _AceHeader(ctypes.Structure):
        _fields_ = [
            ("ace_type", ctypes.c_ubyte),
            ("ace_flags", ctypes.c_ubyte),
            ("ace_size", wintypes.WORD),
        ]

    class _AccessAllowedAce(ctypes.Structure):
        _fields_ = [
            ("header", _AceHeader),
            ("mask", wintypes.DWORD),
            ("sid_start", wintypes.DWORD),
        ]

    _ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ADVAPI32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    _ADVAPI32.GetNamedSecurityInfoW.restype = wintypes.DWORD
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
    _ADVAPI32.CreateWellKnownSid.argtypes = (
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    )
    _ADVAPI32.CreateWellKnownSid.restype = wintypes.BOOL
    _ADVAPI32.EqualSid.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _ADVAPI32.EqualSid.restype = wintypes.BOOL
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
    _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    _ADVAPI32.SetFileSecurityW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
    )
    _ADVAPI32.SetFileSecurityW.restype = wintypes.BOOL
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
    _KERNEL32.GetCurrentProcess.restype = wintypes.HANDLE
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _KERNEL32.LocalFree.argtypes = (ctypes.c_void_p,)
    _KERNEL32.LocalFree.restype = ctypes.c_void_p


def _windows_user_sid_string() -> str:
    if os.name != "nt":
        raise OSError("Windows SID requested on another platform")
    token = wintypes.HANDLE()
    sid_string = wintypes.LPWSTR()
    try:
        if not _ADVAPI32.OpenProcessToken(
            _KERNEL32.GetCurrentProcess(), 0x0008, ctypes.byref(token),
        ):
            raise OSError(ctypes.get_last_error(), "OpenProcessToken")
        needed = wintypes.DWORD()
        _ADVAPI32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise OSError(ctypes.get_last_error(), "GetTokenInformation(size)")
        token_info = ctypes.create_string_buffer(needed.value)
        if not _ADVAPI32.GetTokenInformation(
            token, 1, token_info, needed, ctypes.byref(needed),
        ):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation")
        current_sid = ctypes.c_void_p.from_buffer(token_info).value
        if not current_sid or not _ADVAPI32.ConvertSidToStringSidW(
            current_sid, ctypes.byref(sid_string),
        ):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW")
        return str(sid_string.value)
    finally:
        if sid_string:
            _KERNEL32.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
        if token:
            _KERNEL32.CloseHandle(token)


def _harden_windows_private_acl(path: Path) -> None:
    """Install a protected owner/SYSTEM/Administrators-only Windows DACL."""
    if os.name != "nt":
        return
    descriptor = ctypes.c_void_p()
    try:
        owner_sid = _windows_user_sid_string()
        sddl = (
            "O:BAG:BAD:P"
            f"(A;OICI;FA;;;{owner_sid})"
            "(A;OICI;FA;;;SY)"
            "(A;OICI;FA;;;BA)"
        )
        if not _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None,
        ):
            raise OSError(
                ctypes.get_last_error(),
                "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            )
        if not _ADVAPI32.SetFileSecurityW(
            str(path),
            0x00000001 | 0x00000004 | 0x80000000,
            descriptor,
        ):
            raise OSError(ctypes.get_last_error(), "SetFileSecurityW")
    except OSError as exc:
        raise BirthKeyStoreError(
            "birth_keystore_unavailable", f"Windows ACL hardening: {path}",
        ) from exc
    finally:
        if descriptor.value:
            _KERNEL32.LocalFree(descriptor)


def _check_windows_acl(path: Path, *, confidential: bool = True) -> None:
    """Reject unrelated Windows write access and, for secrets, read access."""
    if os.name != "nt":
        return

    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    token = wintypes.HANDLE()
    try:
        error = _ADVAPI32.GetNamedSecurityInfoW(
            str(path),
            1,  # SE_FILE_OBJECT
            0x00000001 | 0x00000004,  # OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if error or not owner.value or not dacl.value:
            raise BirthKeyStoreError("birth_keystore_unsafe", f"Windows ACL: {path}")
        if not _ADVAPI32.OpenProcessToken(
            _KERNEL32.GetCurrentProcess(), 0x0008, ctypes.byref(token),
        ):
            raise OSError(ctypes.get_last_error(), "OpenProcessToken")
        needed = wintypes.DWORD()
        _ADVAPI32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise OSError(ctypes.get_last_error(), "GetTokenInformation(size)")
        token_info = ctypes.create_string_buffer(needed.value)
        if not _ADVAPI32.GetTokenInformation(
            token, 1, token_info, needed, ctypes.byref(needed),
        ):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation")
        current_sid = ctypes.c_void_p.from_buffer(token_info).value
        if not current_sid:
            raise BirthKeyStoreError("birth_keystore_unsafe", f"Windows owner: {path}")

        allowed_sids: list[ctypes.c_void_p | int] = [current_sid]
        sid_buffers = []
        for sid_type in (22, 26):  # LocalSystem, Builtin Administrators
            size = wintypes.DWORD(68)
            buffer = ctypes.create_string_buffer(size.value)
            if not _ADVAPI32.CreateWellKnownSid(
                sid_type, None, buffer, ctypes.byref(size),
            ):
                raise OSError(ctypes.get_last_error(), "CreateWellKnownSid")
            sid_buffers.append(buffer)
            allowed_sids.append(ctypes.addressof(buffer))

        def allowed(sid: int | None) -> bool:
            return bool(sid) and any(
                _ADVAPI32.EqualSid(sid, candidate) for candidate in allowed_sids
            )

        if not allowed(owner.value):
            raise BirthKeyStoreError("birth_keystore_unsafe", f"Windows owner: {path}")
        info = _AclSizeInformation()
        if not _ADVAPI32.GetAclInformation(
            dacl, ctypes.byref(info), ctypes.sizeof(info), 2,
        ):
            raise OSError(ctypes.get_last_error(), "GetAclInformation")
        write_mask = (
            0x00000002 | 0x00000004 | 0x00000010 | 0x00000040 | 0x00000100
            | 0x00010000 | 0x00040000 | 0x00080000
            | 0x10000000 | 0x40000000
        )
        read_mask = (
            0x00000001 | 0x00000008 | 0x00000080 | 0x00020000
            | 0x10000000 | 0x80000000
        )
        allowed_types = {0, 5, 9, 11}
        for index in range(info.ace_count):
            pointer = ctypes.c_void_p()
            if not _ADVAPI32.GetAce(dacl, index, ctypes.byref(pointer)):
                raise OSError(ctypes.get_last_error(), "GetAce")
            header = ctypes.cast(pointer, ctypes.POINTER(_AceHeader)).contents
            if header.ace_type not in allowed_types or header.ace_flags & 0x08:
                continue
            if header.ace_type != 0:
                raise BirthKeyStoreError(
                    "birth_keystore_unsafe", f"complex writable ACL: {path}",
                )
            ace = ctypes.cast(pointer, ctypes.POINTER(_AccessAllowedAce)).contents
            sid = pointer.value + _AccessAllowedAce.sid_start.offset
            forbidden_mask = write_mask | (read_mask if confidential else 0)
            if ace.mask & forbidden_mask and not allowed(sid):
                raise BirthKeyStoreError(
                    "birth_keystore_unsafe", f"shared Windows ACL: {path}",
                )
    except BirthKeyStoreError:
        raise
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", "Windows ACL") from exc
    finally:
        if token:
            _KERNEL32.CloseHandle(token)
        if descriptor.value:
            _KERNEL32.LocalFree(descriptor)


class BirthKeyStoreError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class LoadedBirthKeyStore:
    """Immutable signing identity plus the complete historical verifier set."""

    config_revision: int
    active_key_id: str
    active_private_key: Ed25519PrivateKey
    verifier_keys: Mapping[str, Ed25519PublicKey]

    def __post_init__(self) -> None:
        object.__setattr__(self, "verifier_keys", MappingProxyType(dict(self.verifier_keys)))


def raw_public_key(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def birth_key_id(public_key: bytes | Ed25519PublicKey) -> str:
    raw = raw_public_key(public_key) if isinstance(public_key, Ed25519PublicKey) else public_key
    if not isinstance(raw, bytes) or len(raw) != 32:
        raise BirthKeyStoreError("birth_key_invalid", "public key must be exactly 32 bytes")
    return f"birth-ed25519-v{KEY_ID_VERSION}-sha256-{hashlib.sha256(raw).hexdigest()}"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _pairs_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BirthKeyStoreError("birth_keystore_config_invalid", "duplicate JSON key")
        result[key] = value
    return result


def _safe_relative(value: object, *, prefix: str, suffix: str) -> str:
    if not isinstance(value, str) or "\0" in value or "\\" in value:
        raise BirthKeyStoreError("birth_keystore_config_invalid", "unsafe path")
    parsed = PurePosixPath(value)
    if (
        parsed.as_posix() != value or parsed.is_absolute() or ".." in parsed.parts
        or len(parsed.parts) != 2 or parsed.parts[0] != prefix
        or not parsed.parts[1].endswith(suffix)
    ):
        raise BirthKeyStoreError("birth_keystore_config_invalid", "unsafe path")
    return value


def _check_directory(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        try:
            component_info = component.lstat()
        except OSError as exc:
            raise BirthKeyStoreError("birth_keystore_unavailable", str(component)) from exc
        if (
            stat.S_ISLNK(component_info.st_mode)
            or bool(
                getattr(component_info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            or (hasattr(component, "is_junction") and component.is_junction())
        ):
            raise BirthKeyStoreError(
                "birth_keystore_unsafe", f"linked directory component: {component}",
            )
    try:
        info = absolute.lstat()
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", str(absolute)) from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        or (hasattr(absolute, "is_junction") and absolute.is_junction())
    ):
        raise BirthKeyStoreError("birth_keystore_unsafe", f"not a real directory: {absolute}")
    _check_windows_acl(absolute)
    if os.name == "posix":
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            raise BirthKeyStoreError("birth_keystore_unsafe", f"directory permissions: {absolute}")


def _open_checked(path: Path, *, expected_mode: int = 0o600, writable: bool = False) -> int:
    try:
        entry = path.lstat()
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", str(path)) from exc
    if (
        stat.S_ISLNK(entry.st_mode)
        or bool(
            getattr(entry, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    ):
        raise BirthKeyStoreError("birth_keystore_unsafe", f"linked file: {path}")
    _check_windows_acl(path)
    flags = (os.O_RDWR if writable else os.O_RDONLY)
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", str(path)) from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino)
            or bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            raise BirthKeyStoreError("birth_keystore_unsafe", f"file type or links: {path}")
        if os.name == "posix" and (
            stat.S_IMODE(info.st_mode) != expected_mode or info.st_uid != os.geteuid()
        ):
            raise BirthKeyStoreError("birth_keystore_unsafe", f"file permissions: {path}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_checked(path: Path, *, limit: int) -> bytes:
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        try:
            entry = path.lstat()
        except OSError as exc:
            raise BirthKeyStoreError("birth_keystore_unavailable", str(path)) from exc
        if (
            stat.S_ISLNK(entry.st_mode)
            or bool(
                getattr(entry, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            raise BirthKeyStoreError("birth_keystore_unsafe", f"linked file: {path}")
        _check_windows_acl(path)
        try:
            from executor_birth_semantic_authority import _secure_file_bytes
            return _secure_file_bytes(path, maximum=limit, error="birth_keystore_unsafe")
        except Exception as exc:
            raise BirthKeyStoreError("birth_keystore_unsafe", str(path)) from exc
    fd = _open_checked(path)
    try:
        with os.fdopen(fd, "rb", closefd=True) as stream:
            payload = stream.read(limit + 1)
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", str(path)) from exc
    if len(payload) > limit:
        raise BirthKeyStoreError("birth_keystore_unsafe", f"oversized file: {path}")
    return payload


@contextlib.contextmanager
def _store_lock(path: Path) -> Iterator[None]:
    # MSVCRT byte-range locks require a writable descriptor; opening is still
    # non-creating and the lock byte is never modified.
    fd = _open_checked(path, writable=os.name == "nt")
    try:
        if os.name == "nt":  # pragma: no cover - exercised by Windows CI
            import msvcrt
            if os.fstat(fd).st_size < 1:
                raise BirthKeyStoreError("birth_keystore_unsafe", "empty lock file")
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise BirthKeyStoreError(
                            "birth_keystore_unavailable", "lock timeout",
                        ) from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError as exc:
                    raise BirthKeyStoreError(
                        "birth_keystore_unavailable", "lock release",
                    ) from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
            except OSError as exc:
                raise BirthKeyStoreError("birth_keystore_unavailable", "lock") from exc
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError as exc:
                    raise BirthKeyStoreError(
                        "birth_keystore_unavailable", "lock release",
                    ) from exc
    finally:
        os.close(fd)


def _forbidden_raw(values: Iterable[bytes | Ed25519PublicKey]) -> tuple[bytes, ...]:
    result: list[bytes] = []
    for value in values:
        raw = raw_public_key(value) if isinstance(value, Ed25519PublicKey) else value
        if not isinstance(raw, bytes) or len(raw) != 32:
            raise BirthKeyStoreError("birth_key_invalid", "forbidden public key")
        result.append(raw)
    return tuple(result)


def _closed_inventory(root: Path, *, public_files: set[str], private_file: str) -> None:
    """Reject undeclared material, including private keys left by rotation."""
    _check_directory(root / "public")
    _check_directory(root / "private")
    try:
        root_names = {item.name for item in root.iterdir()}
        public_names = {item.name for item in (root / "public").iterdir()}
        private_names = {item.name for item in (root / "private").iterdir()}
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", "inventory") from exc
    if root_names != {CONFIG_BASENAME, LOCK_BASENAME, "private", "public"}:
        raise BirthKeyStoreError("birth_keystore_unsafe", "undeclared root entry")
    if public_names != {PurePosixPath(item).name for item in public_files}:
        raise BirthKeyStoreError("birth_keystore_unsafe", "public inventory mismatch")
    if private_names != {PurePosixPath(private_file).name}:
        raise BirthKeyStoreError("birth_keystore_unsafe", "private inventory mismatch")


def _same_status(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_uid,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_uid,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


class _PosixStoreReader:
    """Anchor every store operation to one immutable root directory handle."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root))
        self.root_fd = -1
        self.root_status: os.stat_result | None = None
        self.public_fd = -1
        self.private_fd = -1
        self.public_status: os.stat_result | None = None
        self.private_status: os.stat_result | None = None

    def __enter__(self) -> "_PosixStoreReader":
        _check_directory(self.root)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            self.root_fd = os.open(self.root, flags)
            self.root_status = os.fstat(self.root_fd)
        except OSError as exc:
            if self.root_fd >= 0:
                os.close(self.root_fd)
            raise BirthKeyStoreError("birth_keystore_unavailable", str(self.root)) from exc
        try:
            self._require_directory(self.root_status, "root")
        except BaseException:
            os.close(self.root_fd)
            self.root_fd = -1
            raise
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        if self.public_fd >= 0:
            os.close(self.public_fd)
        if self.private_fd >= 0:
            os.close(self.private_fd)
        if self.root_fd >= 0:
            os.close(self.root_fd)

    @staticmethod
    def _require_directory(info: os.stat_result, label: str) -> None:
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
        ):
            raise BirthKeyStoreError(
                "birth_keystore_unsafe", f"directory permissions: {label}",
            )

    def _open_directory(self, name: str) -> int:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            entry = os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            if stat.S_ISLNK(entry.st_mode):
                raise BirthKeyStoreError("birth_keystore_unsafe", f"linked directory: {name}")
            descriptor = os.open(name, flags, dir_fd=self.root_fd)
            self._require_directory(os.fstat(descriptor), name)
            return descriptor
        except BirthKeyStoreError:
            if "descriptor" in locals():
                os.close(descriptor)
            raise
        except OSError as exc:
            raise BirthKeyStoreError("birth_keystore_unavailable", name) from exc

    def _open_file(self, directory_fd: int, name: str, *, writable: bool = False) -> int:
        flags = os.O_RDWR if writable else os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(entry.st_mode):
                raise BirthKeyStoreError("birth_keystore_unsafe", f"linked file: {name}")
            descriptor = os.open(name, flags, dir_fd=directory_fd)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
            ):
                raise BirthKeyStoreError(
                    "birth_keystore_unsafe", f"file type or permissions: {name}",
                )
            return descriptor
        except BirthKeyStoreError:
            if "descriptor" in locals():
                os.close(descriptor)
            raise
        except OSError as exc:
            raise BirthKeyStoreError("birth_keystore_unavailable", name) from exc

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        import fcntl

        lock_fd = self._open_file(self.root_fd, LOCK_BASENAME)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH)
            except OSError as exc:
                raise BirthKeyStoreError("birth_keystore_unavailable", LOCK_BASENAME) from exc
            self.public_fd = self._open_directory("public")
            try:
                self.private_fd = self._open_directory("private")
                self.public_status = os.fstat(self.public_fd)
                self.private_status = os.fstat(self.private_fd)
                try:
                    yield
                    self.verify_stable()
                finally:
                    os.close(self.private_fd)
                    self.private_fd = -1
            finally:
                os.close(self.public_fd)
                self.public_fd = -1
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError as exc:
                    raise BirthKeyStoreError(
                        "birth_keystore_unavailable", LOCK_BASENAME,
                    ) from exc
        finally:
            os.close(lock_fd)

    def read(self, relative: str, *, limit: int) -> bytes:
        parsed = PurePosixPath(relative)
        if len(parsed.parts) == 1:
            directory_fd, name = self.root_fd, parsed.name
        elif len(parsed.parts) == 2 and parsed.parts[0] == "public":
            directory_fd, name = self.public_fd, parsed.name
        elif len(parsed.parts) == 2 and parsed.parts[0] == "private":
            directory_fd, name = self.private_fd, parsed.name
        else:
            raise BirthKeyStoreError("birth_keystore_config_invalid", "unsafe path")
        descriptor = self._open_file(directory_fd, name)
        try:
            before = os.fstat(descriptor)
            payload = bytearray()
            while len(payload) <= limit:
                block = os.read(descriptor, min(8192, limit + 1 - len(payload)))
                if not block:
                    break
                payload.extend(block)
            after = os.fstat(descriptor)
            if len(payload) > limit:
                raise BirthKeyStoreError("birth_keystore_unsafe", f"oversized file: {name}")
            if not _same_status(before, after):
                raise BirthKeyStoreError("birth_keystore_unsafe", f"file changed: {name}")
            return bytes(payload)
        except OSError as exc:
            raise BirthKeyStoreError("birth_keystore_unavailable", name) from exc
        finally:
            os.close(descriptor)

    def inventory(self, *, public_files: set[str], private_file: str) -> None:
        try:
            root_names = set(os.listdir(self.root_fd))
            public_names = set(os.listdir(self.public_fd))
            private_names = set(os.listdir(self.private_fd))
        except OSError as exc:
            raise BirthKeyStoreError("birth_keystore_unavailable", "inventory") from exc
        if root_names != {CONFIG_BASENAME, LOCK_BASENAME, "private", "public"}:
            raise BirthKeyStoreError("birth_keystore_unsafe", "undeclared root entry")
        if public_names != {PurePosixPath(item).name for item in public_files}:
            raise BirthKeyStoreError("birth_keystore_unsafe", "public inventory mismatch")
        if private_names != {PurePosixPath(private_file).name}:
            raise BirthKeyStoreError("birth_keystore_unsafe", "private inventory mismatch")

    def verify_stable(self) -> None:
        if self.root_status is None:
            raise BirthKeyStoreError("birth_keystore_unsafe", "root identity")
        try:
            by_handle = os.fstat(self.root_fd)
            by_path = self.root.lstat()
        except OSError as exc:
            raise BirthKeyStoreError("birth_keystore_unavailable", "root identity") from exc
        if not _same_status(self.root_status, by_handle) or (
            by_handle.st_dev,
            by_handle.st_ino,
        ) != (
            by_path.st_dev,
            by_path.st_ino,
        ):
            raise BirthKeyStoreError("birth_keystore_unsafe", "root changed")
        if (
            self.public_status is None
            or self.private_status is None
            or not _same_status(self.public_status, os.fstat(self.public_fd))
            or not _same_status(self.private_status, os.fstat(self.private_fd))
        ):
            raise BirthKeyStoreError("birth_keystore_unsafe", "key directory changed")


def _load_locked_store(
    *,
    read: Callable[..., bytes],
    inventory: Callable[..., None],
    forbidden: tuple[bytes, ...],
) -> LoadedBirthKeyStore:
    encoded = read(CONFIG_BASENAME, limit=64 * 1024)
    try:
        config = json.loads(encoded.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except BirthKeyStoreError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BirthKeyStoreError("birth_keystore_config_invalid", "JSON") from exc
    if not isinstance(config, dict) or _canonical(config) != encoded:
        raise BirthKeyStoreError("birth_keystore_config_invalid", "non-canonical encoding")
    if set(config) != {
        "active_key_id", "config_revision", "keys", "private_file", "schema_version",
    }:
        raise BirthKeyStoreError("birth_keystore_config_invalid", "top-level schema")
    revision = config["config_revision"]
    if (
        config["schema_version"] != SCHEMA_VERSION
        or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1
        or not isinstance(config["active_key_id"], str)
        or not isinstance(config["keys"], list) or not config["keys"]
    ):
        raise BirthKeyStoreError("birth_keystore_config_invalid", "top-level values")
    active_id = config["active_key_id"]
    private_file = _safe_relative(config["private_file"], prefix="private", suffix=".key")
    if private_file != f"private/{active_id}.key":
        raise BirthKeyStoreError("birth_keystore_config_invalid", "active private path")

    verifier_keys: dict[str, Ed25519PublicKey] = {}
    public_files: set[str] = set()
    active_count = 0
    for entry in config["keys"]:
        if not isinstance(entry, dict) or set(entry) != {"key_id", "public_file", "status"}:
            raise BirthKeyStoreError("birth_keystore_config_invalid", "key schema")
        key_id = entry["key_id"]
        if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
            raise BirthKeyStoreError("birth_keystore_config_invalid", "key id")
        if key_id in verifier_keys or entry["status"] not in {"active", "verifier"}:
            raise BirthKeyStoreError("birth_keystore_config_invalid", "duplicate key or status")
        public_file = _safe_relative(entry["public_file"], prefix="public", suffix=".pub")
        if public_file != f"public/{key_id}.pub":
            raise BirthKeyStoreError("birth_keystore_config_invalid", "public path")
        public_files.add(public_file)
        raw = read(public_file, limit=32)
        if len(raw) != 32 or not hmac.compare_digest(key_id, birth_key_id(raw)):
            raise BirthKeyStoreError("birth_key_invalid", key_id)
        if any(hmac.compare_digest(raw, item) for item in forbidden):
            raise BirthKeyStoreError("birth_key_reuses_author_identity", key_id)
        try:
            verifier_keys[key_id] = Ed25519PublicKey.from_public_bytes(raw)
        except ValueError as exc:
            raise BirthKeyStoreError("birth_key_invalid", key_id) from exc
        if entry["status"] == "active":
            active_count += 1
            if key_id != active_id:
                raise BirthKeyStoreError("birth_keystore_config_invalid", "active mismatch")
    if active_count != 1 or active_id not in verifier_keys:
        raise BirthKeyStoreError("birth_keystore_config_invalid", "exactly one active key required")
    if [entry["key_id"] for entry in config["keys"]] != sorted(verifier_keys):
        raise BirthKeyStoreError("birth_keystore_config_invalid", "keyring order")

    inventory(public_files=public_files, private_file=private_file)
    private_raw = read(private_file, limit=32)
    if len(private_raw) != 32:
        raise BirthKeyStoreError("birth_key_invalid", "private key must be exactly 32 bytes")
    try:
        private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
    except ValueError as exc:
        raise BirthKeyStoreError("birth_key_invalid", "private key") from exc
    derived = raw_public_key(private_key.public_key())
    declared = raw_public_key(verifier_keys[active_id])
    if not hmac.compare_digest(derived, declared):
        raise BirthKeyStoreError("birth_key_pair_mismatch", active_id)

    return LoadedBirthKeyStore(
        config_revision=revision,
        active_key_id=active_id,
        active_private_key=private_key,
        verifier_keys=verifier_keys,
    )


def load_birth_keystore(
    root: Path,
    *,
    forbidden_public_keys: Iterable[bytes | Ed25519PublicKey] = (),
) -> LoadedBirthKeyStore:
    """Load a pre-provisioned store; fail closed on any ambiguity or mutation.

    ``forbidden_public_keys`` is the bootstrap-owned set of author identities.
    Supplying it makes separation from those identities cryptographically
    explicit; this function never discovers or loads an author private key.
    """
    root = Path(root)
    forbidden = _forbidden_raw(forbidden_public_keys)
    if os.name == "posix":
        with _PosixStoreReader(root) as reader:
            with reader.locked():
                return _load_locked_store(
                    read=reader.read,
                    inventory=reader.inventory,
                    forbidden=forbidden,
                )

    _check_directory(root)
    with _store_lock(root / LOCK_BASENAME):
        return _load_locked_store(
            read=lambda relative, *, limit: _read_checked(root / relative, limit=limit),
            inventory=lambda *, public_files, private_file: _closed_inventory(
                root,
                public_files=public_files,
                private_file=private_file,
            ),
            forbidden=forbidden,
        )
