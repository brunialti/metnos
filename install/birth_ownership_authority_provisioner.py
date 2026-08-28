"""Recoverable installer-side provisioning of the three F4 deployment keys.

The productive entry has one fixed destination and requires root.  Tests use
the capability core with an isolated directory, but the codec, persistence,
checkpoint and cold-loader paths are the same.
"""
from __future__ import annotations

import os
import stat
import ctypes
import errno
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - installer import bootstrap
    sys.path.insert(0, str(_RUNTIME))

from executor_birth_ownership_authorities import (
    AUTHORITY_DIRECTORY_BASENAME_V1, DEFAULT_OWNERSHIP_ROOT_V1,
    OwnershipAuthorityError, RootOwnershipAuthoritiesV1,
    _CHECKPOINT_BASENAMES, _KINDS, _PRIVATE_BASENAMES, _REGISTRY_BASENAMES,
    _birth_public_keys_v1, _checkpoint_payload, _directory_metadata,
    _load_private_at_v1, _read_regular, _require_no_reused_public_keys_v1,
    _root_owned_chain, _managed_authority_platform_supported_v1,
    decode_ownership_registry_v1, encode_ownership_registry_v1,
)


_PENDING_BASENAME_V1 = ".authorities-v1.pending"
_LOCK_BASENAME_V1 = ".authorities-v1.lock"
_CrashHook = Callable[[str], None]
_IdentityV1 = tuple[int, int, int, int, int, int, int]


def _identity(info: os.stat_result) -> _IdentityV1:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _stable_identity(identity: _IdentityV1) -> tuple[int, int, int, int, int]:
    return identity[:5]


def _sync_directory(path: Path) -> None:
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _path_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", path.name,
        ) from exc
    return True


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp")


def _read_temporary(
    path: Path, *, maximum: int, mode: int, root_owned: bool,
) -> tuple[bytes, _IdentityV1]:
    flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", path.name,
        ) from exc
    try:
        before = os.fstat(fd)
        observed_mode = stat.S_IMODE(before.st_mode)
        safe_mode = (
            observed_mode == mode
            or (
                before.st_size == 0
                and observed_mode & ~mode == 0
            )
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not safe_mode
            or before.st_size > maximum
            or (root_owned and (before.st_uid != 0 or before.st_gid != 0))
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", path.name,
            )
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(fd)
        if (
            len(payload) > maximum
            or len(payload) != before.st_size
            or _identity(before) != _identity(after)
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", path.name,
            )
        return bytes(payload), _identity(after)
    finally:
        os.close(fd)


def _publish_no_replace(
    temporary: Path, destination: Path, *, crash: _CrashHook | None,
    stage: str, expected_identity: _IdentityV1,
) -> None:
    """Linux atomic publication for both files and directories."""
    if not sys.platform.startswith("linux"):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required",
            "no-replace rename unavailable",
        )
    try:
        if _identity(temporary.lstat()) != expected_identity:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", "temporary changed",
            )
    except OwnershipAuthorityError:
        raise
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "temporary",
        ) from exc
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required",
            "no-replace rename unavailable",
        )
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100, os.fsencode(temporary), -100, os.fsencode(destination), 1,
    )
    if result != 0:
        number = ctypes.get_errno()
        detail = "publish conflict" if number == errno.EEXIST else "publish"
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", detail,
        ) from OSError(number, os.strerror(number), destination)
    if crash is not None:
        crash(stage)
    try:
        if (
            _stable_identity(_identity(destination.lstat()))
            != _stable_identity(expected_identity)
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", "published identity",
            )
    except OwnershipAuthorityError:
        raise
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "published object",
        ) from exc
    _sync_directory(destination.parent)


def _discard_temporary(path: Path) -> None:
    try:
        path.unlink()
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", path.name,
        ) from exc
    _sync_directory(path.parent)


@contextmanager
def _provisioning_lock(root: Path, *, root_owned: bool):
    """Authenticate and exclusively hold the transaction namespace."""
    import fcntl

    path = root / _LOCK_BASENAME_V1
    flags = (
        os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    created = False
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "lock",
            ) from exc
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "lock",
        ) from exc
    try:
        if created:
            os.fchmod(fd, 0o600)
            os.fsync(fd)
            _sync_directory(root)
        info = os.fstat(fd)
        path_info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
            or (root_owned and (info.st_uid != 0 or info.st_gid != 0))
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", "lock",
            )
        fcntl.flock(fd, fcntl.LOCK_EX)
        after = os.fstat(fd)
        if (
            (after.st_dev, after.st_ino, after.st_mode, after.st_nlink)
            != (info.st_dev, info.st_ino, info.st_mode, info.st_nlink)
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", "lock changed",
            )
        yield
    except OwnershipAuthorityError:
        raise
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "lock",
        ) from exc
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_exclusive(
    path: Path, payload: bytes, mode: int, *, root_owned: bool,
    crash: _CrashHook | None,
) -> None:
    temporary = _temporary_path(path)
    if _path_present(path):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", path.name,
        )
    if _path_present(temporary):
        staged, staged_identity = _read_temporary(
            temporary, maximum=len(payload), mode=mode,
            root_owned=root_owned,
        )
        if staged == payload:
            try:
                fd = os.open(
                    temporary,
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_BINARY", 0),
                )
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                raise OwnershipAuthorityError(
                    "birth_ownership_authority_recovery_required",
                    temporary.name,
                ) from exc
            if crash is not None:
                crash(f"after_{path.name}_temp_fsync")
            _sync_directory(path.parent)
            if crash is not None:
                crash(f"after_{path.name}_temp_directory_fsync")
            _publish_no_replace(
                temporary, path, crash=crash,
                stage=f"after_{path.name}_rename",
                expected_identity=staged_identity,
            )
            return
        if len(staged) < len(payload):
            _discard_temporary(temporary)
        else:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", path.name,
            )
    flags = (
        os.O_RDWR | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        fd = os.open(temporary, flags, mode)
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", temporary.name,
        ) from exc
    try:
        if crash is not None:
            crash(f"after_{path.name}_temp_open")
        os.fchmod(fd, mode)
        if crash is not None:
            crash(f"after_{path.name}_temp_create")
        offset = 0
        split = max(1, len(payload) // 2)
        while offset < split:
            written = os.write(fd, payload[offset:split])
            if written <= 0:
                raise OSError("short write")
            offset += written
        if crash is not None:
            crash(f"after_{path.name}_temp_prefix")
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        if crash is not None:
            crash(f"after_{path.name}_temp_full_write")
        os.fsync(fd)
        if crash is not None:
            crash(f"after_{path.name}_temp_fsync")
        os.lseek(fd, 0, os.SEEK_SET)
        observed = bytearray()
        while len(observed) < len(payload):
            chunk = os.read(fd, len(payload) - len(observed))
            if not chunk:
                break
            observed.extend(chunk)
        if bytes(observed) != payload or os.read(fd, 1):
            raise OSError("temporary reread mismatch")
        verified_identity = _identity(os.fstat(fd))
        if crash is not None:
            crash(f"after_{path.name}_temp_verify")
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", path.name,
        ) from exc
    finally:
        os.close(fd)
    _sync_directory(path.parent)
    if crash is not None:
        crash(f"after_{path.name}_temp_directory_fsync")
    _publish_no_replace(
        temporary, path, crash=crash, stage=f"after_{path.name}_rename",
        expected_identity=verified_identity,
    )


def _private_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _pending_metadata(path: Path, *, root_owned: bool) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "pending directory",
        ) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & 0x400)
        or stat.S_IMODE(info.st_mode) not in {0o700, 0o755}
        or info.st_mode & 0o022
        or (root_owned and (info.st_uid != 0 or info.st_gid != 0))
    ):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_unsafe", "pending directory",
        )


def _checkpoint_prefix(
    pending: Path, *, root_owned: bool,
) -> int:
    """Return the last contiguous checkpoint and reject every unknown object."""
    try:
        names = {item.name for item in pending.iterdir()}
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "pending inventory",
        ) from exc
    present = []
    for index, basename in enumerate(_CHECKPOINT_BASENAMES):
        if basename not in names:
            break
        payload = _read_regular(
            pending / basename, maximum=4096, mode=0o644,
            root_owned=root_owned,
        )
        if payload != _checkpoint_payload(index):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "checkpoint",
            )
        present.append(index)
    if any(
        basename in names for basename in _CHECKPOINT_BASENAMES[len(present):]
    ):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "checkpoint gap",
        )
    allowed = set(_CHECKPOINT_BASENAMES[:len(present)])
    if not present:
        allowed.add(_temporary_path(pending / _CHECKPOINT_BASENAMES[0]).name)
    completed = min(present[-1] if present else 0, len(_KINDS))
    for kind in _KINDS[:completed]:
        allowed.update({_PRIVATE_BASENAMES[kind], _REGISTRY_BASENAMES[kind]})
    if present and completed < len(_KINDS):
        kind = _KINDS[completed]
        private_name = _PRIVATE_BASENAMES[kind]
        registry_name = _REGISTRY_BASENAMES[kind]
        if private_name in names:
            allowed.add(private_name)
            if registry_name in names:
                allowed.add(registry_name)
                allowed.add(_temporary_path(
                    pending / _CHECKPOINT_BASENAMES[completed + 1]
                ).name)
            else:
                allowed.add(_temporary_path(pending / registry_name).name)
        else:
            allowed.add(_temporary_path(pending / private_name).name)
    elif present and present[-1] == len(_KINDS):
        allowed.add(_temporary_path(pending / _CHECKPOINT_BASENAMES[-1]).name)
    if names - allowed:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "pending inventory",
        )
    return present[-1] if present else -1


def _load_or_create_pair(
    pending: Path, kind: str, *, root_owned: bool,
    crash: _CrashHook | None,
) -> None:
    private_path = pending / _PRIVATE_BASENAMES[kind]
    registry_path = pending / _REGISTRY_BASENAMES[kind]
    private_exists = _path_present(private_path)
    registry_exists = _path_present(registry_path)
    if registry_exists and not private_exists:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "registry without key",
        )
    if private_exists:
        encoded_private = _read_regular(
            private_path, maximum=32, mode=0o600, root_owned=root_owned,
        )
        if len(encoded_private) != 32:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "private key",
            )
        try:
            private = Ed25519PrivateKey.from_private_bytes(encoded_private)
        except ValueError as exc:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "private key",
            ) from exc
    else:
        private_temporary = _temporary_path(private_path)
        private = None
        if _path_present(private_temporary):
            staged_private, _staged_identity = _read_temporary(
                private_temporary, maximum=32, mode=0o600,
                root_owned=root_owned,
            )
            if len(staged_private) == 32:
                private = Ed25519PrivateKey.from_private_bytes(staged_private)
            else:
                _discard_temporary(private_temporary)
        if private is None:
            private = Ed25519PrivateKey.generate()
        _write_exclusive(
            private_path, _private_bytes(private), 0o600,
            root_owned=root_owned, crash=crash,
        )
        if crash is not None:
            crash(f"after_{kind}_private")
    expected_registry = encode_ownership_registry_v1(kind, private.public_key())
    if registry_exists:
        encoded_registry = _read_regular(
            registry_path, maximum=64 * 1024, mode=0o644,
            root_owned=root_owned,
        )
        decode_ownership_registry_v1(encoded_registry, expected_kind=kind)
        if encoded_registry != expected_registry:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "registry binding",
            )
    else:
        _write_exclusive(
            registry_path, expected_registry, 0o644,
            root_owned=root_owned, crash=crash,
        )
        if crash is not None:
            crash(f"after_{kind}_registry")


def _validate_completed_pairs(
    pending: Path, completed: int, *, root_owned: bool,
) -> None:
    observed: set[bytes] = set()
    for kind in _KINDS[:completed]:
        private_bytes = _read_regular(
            pending / _PRIVATE_BASENAMES[kind], maximum=32, mode=0o600,
            root_owned=root_owned,
        )
        if len(private_bytes) != 32:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "private key",
            )
        private = Ed25519PrivateKey.from_private_bytes(private_bytes)
        registry_bytes = _read_regular(
            pending / _REGISTRY_BASENAMES[kind], maximum=64 * 1024,
            mode=0o644, root_owned=root_owned,
        )
        if registry_bytes != encode_ownership_registry_v1(
            kind, private.public_key(),
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "registry binding",
            )
        decode_ownership_registry_v1(registry_bytes, expected_kind=kind)
        raw = private.public_key().public_bytes_raw()
        if raw in observed:
            raise OwnershipAuthorityError("birth_ownership_authority_key_reused")
        observed.add(raw)


def _provision_ownership_authorities_locked_v1(
    root: Path, *, forbidden_public_keys: Iterable[bytes],
    root_owned: bool, crash: _CrashHook | None = None,
) -> RootOwnershipAuthoritiesV1:
    """Advance one transaction while its authenticated lock is held."""
    final = root / AUTHORITY_DIRECTORY_BASENAME_V1
    pending = root / _PENDING_BASENAME_V1
    if _path_present(final):
        if _path_present(pending):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "double transaction",
            )
        loaded = _load_private_at_v1(final, root_owned=root_owned)
        _require_no_reused_public_keys_v1(loaded, forbidden_public_keys)
        # A previous process may have died after renameat2 and before syncing
        # the parent.  An exact retry repairs that durability barrier.
        _sync_directory(root)
        return loaded
    if not _path_present(pending):
        try:
            pending.mkdir(mode=0o700)
        except OSError as exc:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "create transaction",
            ) from exc
        _sync_directory(root)
        if crash is not None:
            crash("after_directory")
    _pending_metadata(pending, root_owned=root_owned)
    checkpoint = _checkpoint_prefix(pending, root_owned=root_owned)
    if checkpoint < 0:
        _write_exclusive(
            pending / _CHECKPOINT_BASENAMES[0], _checkpoint_payload(0), 0o644,
            root_owned=root_owned, crash=crash,
        )
        checkpoint = 0
        if crash is not None:
            crash("after_checkpoint_0")
    completed = min(checkpoint, len(_KINDS))
    _validate_completed_pairs(pending, completed, root_owned=root_owned)
    for index in range(completed, len(_KINDS)):
        kind = _KINDS[index]
        _load_or_create_pair(
            pending, kind, root_owned=root_owned, crash=crash,
        )
        _validate_completed_pairs(pending, index + 1, root_owned=root_owned)
        next_checkpoint = index + 1
        _write_exclusive(
            pending / _CHECKPOINT_BASENAMES[next_checkpoint],
            _checkpoint_payload(next_checkpoint), 0o644,
            root_owned=root_owned, crash=crash,
        )
        if crash is not None:
            crash(f"after_checkpoint_{next_checkpoint}")
    _validate_completed_pairs(pending, len(_KINDS), root_owned=root_owned)
    if (
        checkpoint < len(_KINDS) + 1
        and not _path_present(pending / _CHECKPOINT_BASENAMES[-1])
    ):
        _write_exclusive(
            pending / _CHECKPOINT_BASENAMES[-1],
            _checkpoint_payload(len(_KINDS) + 1), 0o644,
            root_owned=root_owned, crash=crash,
        )
        if crash is not None:
            crash("after_verified_checkpoint")
    try:
        pending.chmod(0o755)
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "directory mode",
        ) from exc
    _sync_directory(pending)
    if crash is not None:
        crash("after_directory_metadata_fsync")
    staged = _load_private_at_v1(pending, root_owned=root_owned)
    _require_no_reused_public_keys_v1(staged, forbidden_public_keys)
    if crash is not None:
        crash("before_publish")
    _publish_no_replace(
        pending, final, crash=crash, stage="after_directory_rename",
        expected_identity=_identity(pending.lstat()),
    )
    if crash is not None:
        crash("after_publish")
    loaded = _load_private_at_v1(final, root_owned=root_owned)
    _require_no_reused_public_keys_v1(loaded, forbidden_public_keys)
    return loaded


def _provision_ownership_authorities_at_v1(
    root: Path, *, forbidden_public_keys: Iterable[bytes],
    root_owned: bool, crash: _CrashHook | None = None,
) -> RootOwnershipAuthoritiesV1:
    """Capability core shared by the fixed resolver and isolated certification."""
    root = Path(root)
    _directory_metadata(root, root_owned=root_owned)
    with _provisioning_lock(root, root_owned=root_owned):
        return _provision_ownership_authorities_locked_v1(
            root, forbidden_public_keys=forbidden_public_keys,
            root_owned=root_owned, crash=crash,
        )


def provision_root_ownership_authorities_v1() -> RootOwnershipAuthoritiesV1:
    """Provision the fixed deployment authorities from authenticated Birth trust."""
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError(
            "birth_ownership_authority_platform_unsupported",
        )
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise OwnershipAuthorityError("birth_ownership_authority_root_required")
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    forbidden = _birth_public_keys_v1()
    return _provision_ownership_authorities_at_v1(
        DEFAULT_OWNERSHIP_ROOT_V1, forbidden_public_keys=forbidden,
        root_owned=True,
    )


__all__ = ["provision_root_ownership_authorities_v1"]
