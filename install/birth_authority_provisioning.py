# SPDX-License-Identifier: AGPL-3.0-only
"""External, fail-closed provisioning for Executor Birth authorities.

Runtime loaders never create or repair key material. This installer-owned
module performs the one-time migration of the legacy author trust ring into a
closed Birth keystore. Migration is serialized, crash-recoverable and exact;
later verification reads only the installed Birth root and no longer depends
on the retired legacy private key.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Iterator

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


_LEGACY_KEY = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)_(priv|pub)\.bin\Z")
_AUTHOR_TARGET = "author-keystore"
_PROVISION_LOCK = "authority-provisioning.lock"
_STAGING_PREFIX = ".author-keystore.staging."
_LOCK_TIMEOUT_SECONDS = 5.0


class BirthAuthorityProvisioningError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _linked(path: Path, info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or (hasattr(path, "is_junction") and path.is_junction())
    )


def _same_status(before: os.stat_result, after: os.stat_result) -> bool:
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_size",
        "st_mtime_ns", "st_ctime_ns",
    )
    return all(getattr(before, name) == getattr(after, name) for name in fields)


def _safe_existing_directory(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        try:
            info = component.lstat()
        except OSError as exc:
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unavailable", str(component),
            ) from exc
        if _linked(component, info):
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unsafe", str(component),
            )
    info = absolute.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unsafe", str(absolute),
        )
    if os.name == "posix" and (
        stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid()
    ):
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unsafe", str(absolute),
        )
    if os.name == "nt":
        try:
            from executor_birth_keystore import _check_windows_acl
            _check_windows_acl(absolute)
        except Exception as exc:
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unsafe", str(absolute),
            ) from exc
    return absolute


def _ensure_birth_root(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    parent = _safe_existing_directory(absolute.parent)
    parent_before = parent.lstat()
    parent_descriptor = -1
    created = False
    try:
        if os.name == "posix":
            parent_descriptor = os.open(
                parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            opened_parent = os.fstat(parent_descriptor)
            if (
                (opened_parent.st_dev, opened_parent.st_ino)
                != (parent_before.st_dev, parent_before.st_ino)
                or not stat.S_ISDIR(opened_parent.st_mode)
            ):
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe", str(parent),
                )
            os.mkdir(absolute.name, 0o700, dir_fd=parent_descriptor)
        else:
            os.mkdir(absolute, 0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unavailable", str(absolute),
        ) from exc
    finally:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    if created and os.name == "nt":
        try:
            from executor_birth_keystore import _harden_windows_private_acl
            _harden_windows_private_acl(absolute)
        except Exception as exc:
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unavailable", str(absolute),
            ) from exc
    result = _safe_existing_directory(absolute)
    checked_parent = _safe_existing_directory(parent)
    parent_after = checked_parent.lstat()
    if (
        checked_parent != parent
        or (parent_before.st_dev, parent_before.st_ino)
        != (parent_after.st_dev, parent_after.st_ino)
    ):
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unsafe", str(parent),
        )
    return result


def _open_lock_file(root: Path) -> int:
    path = root / _PROVISION_LOCK
    base_flags = (
        os.O_RDWR | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        descriptor = -1
        created = False
        try:
            try:
                descriptor = os.open(
                    path, base_flags | os.O_CREAT | os.O_EXCL, 0o600,
                )
                created = True
            except FileExistsError:
                descriptor = os.open(path, base_flags)
            if created and os.name == "nt":
                from executor_birth_keystore import _harden_windows_private_acl
                _harden_windows_private_acl(path)
            before = path.lstat()
            after = os.fstat(descriptor)
            if (
                _linked(path, before)
                or not stat.S_ISREG(after.st_mode)
                or after.st_nlink != 1
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or (os.name == "posix" and (
                    stat.S_IMODE(after.st_mode) != 0o600
                    or after.st_uid != os.geteuid()
                ))
            ):
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe", _PROVISION_LOCK,
                )
            if os.name == "nt":
                from executor_birth_keystore import _check_windows_acl
                try:
                    _check_windows_acl(path)
                except Exception as exc:
                    raise BirthAuthorityProvisioningError(
                        "birth_author_provisioning_unsafe", _PROVISION_LOCK,
                    ) from exc
            if not created and after.st_size == 0:
                os.close(descriptor)
                descriptor = -1
                if time.monotonic() >= deadline:
                    raise BirthAuthorityProvisioningError(
                        "birth_author_provisioning_unavailable",
                        "lock initialization timeout",
                    )
                time.sleep(0.01)
                continue
            if after.st_size != (0 if created else 1):
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe", _PROVISION_LOCK,
                )
            if created:
                if os.write(descriptor, b"0") != 1:
                    raise OSError("short lock initialization")
                os.fsync(descriptor)
                initialized = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(initialized.st_mode)
                    or initialized.st_nlink != 1
                    or initialized.st_size != 1
                ):
                    raise BirthAuthorityProvisioningError(
                        "birth_author_provisioning_unsafe", _PROVISION_LOCK,
                    )
            return descriptor
        except BirthAuthorityProvisioningError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except Exception as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unavailable", _PROVISION_LOCK,
            ) from exc


@contextlib.contextmanager
def _provisioning_lock(root: Path) -> Iterator[None]:
    descriptor = _open_lock_file(root)
    try:
        if os.name == "nt":  # pragma: no cover - exercised by Windows CI
            import msvcrt
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise BirthAuthorityProvisioningError(
                            "birth_author_provisioning_unavailable", "lock timeout",
                        ) from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except OSError as exc:
                    raise BirthAuthorityProvisioningError(
                        "birth_author_provisioning_unavailable", "lock release",
                    ) from exc
        else:
            import fcntl
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as exc:
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unavailable", "lock",
                ) from exc
            try:
                yield
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError as exc:
                    raise BirthAuthorityProvisioningError(
                        "birth_author_provisioning_unavailable", "lock release",
                    ) from exc
    finally:
        os.close(descriptor)


def _read_descriptor(descriptor: int, *, name: str) -> bytes:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size != 32
    ):
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unsafe", name,
        )
    payload = bytearray()
    while len(payload) <= 32:
        block = os.read(descriptor, 33 - len(payload))
        if not block:
            break
        payload.extend(block)
    after = os.fstat(descriptor)
    if len(payload) != 32 or not _same_status(before, after):
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unsafe", name,
        )
    return bytes(payload)


def _snapshot_legacy_posix(root: Path) -> dict[tuple[str, str], bytes]:
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_descriptor = os.open(root, flags)
    except OSError as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unavailable", "legacy root",
        ) from exc
    try:
        root_before = os.fstat(root_descriptor)
        names_before = tuple(sorted(os.listdir(root_descriptor)))
        result: dict[tuple[str, str], bytes] = {}
        for name in names_before:
            match = _LEGACY_KEY.fullmatch(name)
            if match is None:
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe",
                    f"undeclared legacy key: {name}",
                )
            private = match.group(2) == "priv"
            try:
                entry = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
                descriptor = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=root_descriptor,
                )
            except OSError as exc:
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe", name,
                ) from exc
            try:
                opened = os.fstat(descriptor)
                allowed_modes = {0o600} if private else {0o600, 0o644}
                if (
                    stat.S_ISLNK(entry.st_mode)
                    or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
                    or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) not in allowed_modes
                ):
                    raise BirthAuthorityProvisioningError(
                        "birth_author_provisioning_unsafe", name,
                    )
                result[(match.group(1), match.group(2))] = _read_descriptor(
                    descriptor, name=name,
                )
            finally:
                os.close(descriptor)
        names_after = tuple(sorted(os.listdir(root_descriptor)))
        root_after = os.fstat(root_descriptor)
        path_after = root.lstat()
        if (
            names_before != names_after
            or not _same_status(root_before, root_after)
            or (root_after.st_dev, root_after.st_ino)
            != (path_after.st_dev, path_after.st_ino)
        ):
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unsafe", "legacy registry changed",
            )
        return result
    except OSError as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unavailable", "legacy inventory",
        ) from exc
    finally:
        os.close(root_descriptor)


def _snapshot_legacy_windows(root: Path) -> dict[tuple[str, str], bytes]:
    from executor_birth_keystore import _check_windows_acl
    from executor_birth_semantic_authority import _secure_file_bytes

    try:
        root_before = root.lstat()
        names_before = tuple(sorted(item.name for item in root.iterdir()))
        result: dict[tuple[str, str], bytes] = {}
        for name in names_before:
            match = _LEGACY_KEY.fullmatch(name)
            if match is None:
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe",
                    f"undeclared legacy key: {name}",
                )
            path = root / name
            info = path.lstat()
            if _linked(path, info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe", name,
                )
            _check_windows_acl(path)
            raw = _secure_file_bytes(
                path, maximum=32, error="birth_author_provisioning_unsafe",
            )
            if len(raw) != 32:
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe", name,
                )
            result[(match.group(1), match.group(2))] = raw
        names_after = tuple(sorted(item.name for item in root.iterdir()))
        root_after = root.lstat()
        if names_before != names_after or not _same_status(root_before, root_after):
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unsafe", "legacy registry changed",
            )
        return result
    except BirthAuthorityProvisioningError:
        raise
    except Exception as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unsafe", "legacy inventory",
        ) from exc


def _legacy_author_material(
    legacy_keys_dir: Path,
) -> tuple[bytes, tuple[bytes, ...]]:
    root = _safe_existing_directory(legacy_keys_dir)
    parsed = (
        _snapshot_legacy_posix(root)
        if os.name == "posix"
        else _snapshot_legacy_windows(root)
    )
    if ("author", "priv") not in parsed or ("author", "pub") not in parsed:
        raise BirthAuthorityProvisioningError("birth_author_identity_incomplete")
    if any((name, "pub") not in parsed for name, kind in parsed if kind == "priv"):
        raise BirthAuthorityProvisioningError(
            "birth_author_legacy_registry_incomplete",
        )
    public_by_name = {
        name: payload for (name, kind), payload in parsed.items() if kind == "pub"
    }
    if len(set(public_by_name.values())) != len(public_by_name):
        raise BirthAuthorityProvisioningError(
            "birth_author_legacy_registry_ambiguous",
        )
    for (name, kind), private_raw in parsed.items():
        if kind != "priv":
            continue
        try:
            derived = Ed25519PrivateKey.from_private_bytes(
                private_raw,
            ).public_key().public_bytes_raw()
        except ValueError as exc:
            raise BirthAuthorityProvisioningError(
                "birth_author_identity_invalid", name,
            ) from exc
        if derived != public_by_name[name]:
            raise BirthAuthorityProvisioningError(
                "birth_author_identity_mismatch", name,
            )
    return parsed[("author", "priv")], tuple(sorted(public_by_name.values()))


def _write_new_file(path: Path, payload: bytes) -> None:
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            if os.name == "nt":
                from executor_birth_keystore import _check_windows_acl
                try:
                    _check_windows_acl(path, confidential=True)
                except Exception as exc:
                    raise BirthAuthorityProvisioningError(
                        "birth_author_provisioning_unsafe", path.name,
                    ) from exc
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
            os.fsync(descriptor)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    except BirthAuthorityProvisioningError:
        raise
    except Exception as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unavailable", path.name,
        ) from exc


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _expected_store(
    private_raw: bytes,
    public_ring: tuple[bytes, ...],
) -> tuple[dict, str]:
    from executor_birth_keystore import birth_key_id

    private = Ed25519PrivateKey.from_private_bytes(private_raw)
    active_public = private.public_key().public_bytes_raw()
    active_id = birth_key_id(active_public)
    records = []
    for public in public_ring:
        key_id = birth_key_id(public)
        records.append({
            "key_id": key_id,
            "public_file": f"public/{key_id}.pub",
            "status": "active" if key_id == active_id else "verifier",
        })
    records.sort(key=lambda item: item["key_id"])
    return ({
        "active_key_id": active_id,
        "config_revision": 1,
        "keys": records,
        "private_file": f"private/{active_id}.key",
        "schema_version": 1,
    }, active_id)


def _store_matches(
    root: Path,
    *,
    private_raw: bytes,
    public_ring: tuple[bytes, ...],
) -> bool:
    from executor_birth_keystore import load_birth_keystore, raw_public_key

    loaded = load_birth_keystore(root)
    return bool(
        loaded.active_private_key.private_bytes_raw() == private_raw
        and {raw_public_key(key) for key in loaded.verifier_keys.values()}
        == set(public_ring)
    )


def _stage_candidates(root: Path) -> tuple[Path, ...]:
    try:
        return tuple(sorted(
            (item for item in root.iterdir() if item.name.startswith(_STAGING_PREFIX)),
            key=lambda item: item.name,
        ))
    except OSError as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unavailable", "staging inventory",
        ) from exc


def _remove_stage(path: Path) -> None:
    try:
        info = path.lstat()
        if _linked(path, info) or not stat.S_ISDIR(info.st_mode):
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unsafe", path.name,
            )
        for directory, dirs, files in os.walk(path, topdown=True, followlinks=False):
            for name in (*dirs, *files):
                child = Path(directory) / name
                child_info = child.lstat()
                if _linked(child, child_info) or not (
                    stat.S_ISDIR(child_info.st_mode)
                    or stat.S_ISREG(child_info.st_mode)
                ):
                    raise BirthAuthorityProvisioningError(
                        "birth_author_provisioning_unsafe", child.name,
                    )
        shutil.rmtree(path)
    except BirthAuthorityProvisioningError:
        raise
    except OSError as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_provisioning_unavailable", path.name,
        ) from exc


def _install_directory(source: Path, target: Path) -> None:
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        import ctypes
        from ctypes import wintypes

        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        move.restype = wintypes.BOOL
        if not move(str(source), str(target), 0x00000008):
            raise OSError(ctypes.get_last_error(), "MoveFileExW")
        return
    os.rename(source, target)


def inspect_author_keystore(*, birth_dir: Path) -> dict[str, object]:
    """Verify the installed authority without consulting legacy material."""
    from executor_birth_keystore import BirthKeyStoreError, load_birth_keystore

    target = Path(os.path.abspath(birth_dir)) / _AUTHOR_TARGET
    try:
        target.lstat()
    except FileNotFoundError as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_keystore_unavailable", str(target),
        ) from exc
    except OSError as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_keystore_existing_invalid", str(target),
        ) from exc
    try:
        loaded = load_birth_keystore(target)
    except BirthKeyStoreError as exc:
        raise BirthAuthorityProvisioningError(
            "birth_author_keystore_existing_invalid",
            exc.detail,
        ) from exc
    return {
        "active_key_id": loaded.active_key_id,
        "created": False,
        "verifiers": len(loaded.verifier_keys),
    }


def _build_stage(
    temporary: Path,
    *,
    config: dict,
    active_id: str,
    private_raw: bytes,
    public_ring: tuple[bytes, ...],
) -> None:
    from executor_birth_keystore import birth_key_id

    if os.name == "posix":
        temporary.chmod(0o700)
    private_dir = temporary / "private"
    public_dir = temporary / "public"
    if os.name == "nt":
        from executor_birth_keystore import _harden_windows_private_acl
        try:
            _harden_windows_private_acl(temporary)
        except Exception as exc:
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unavailable", temporary.name,
            ) from exc
    private_dir.mkdir(mode=0o700)
    public_dir.mkdir(mode=0o700)
    if os.name == "nt":
        try:
            _harden_windows_private_acl(private_dir)
            _harden_windows_private_acl(public_dir)
        except Exception as exc:
            raise BirthAuthorityProvisioningError(
                "birth_author_provisioning_unavailable", temporary.name,
            ) from exc
    _write_new_file(temporary / "birth-keystore.lock", b"0")
    for public in public_ring:
        key_id = birth_key_id(public)
        _write_new_file(public_dir / f"{key_id}.pub", public)
    _write_new_file(private_dir / f"{active_id}.key", private_raw)
    _write_new_file(temporary / "keystore.json", _canonical(config))
    _sync_directory(private_dir)
    _sync_directory(public_dir)
    _sync_directory(temporary)


def provision_author_keystore(
    *,
    legacy_keys_dir: Path,
    birth_dir: Path,
) -> dict[str, object]:
    """Migrate the stable legacy author and verifier ring exactly once."""
    from executor_birth_keystore import BirthKeyStoreError

    birth_root = _ensure_birth_root(birth_dir)
    with _provisioning_lock(birth_root):
        target = birth_root / _AUTHOR_TARGET
        try:
            inspected = inspect_author_keystore(birth_dir=birth_root)
        except BirthAuthorityProvisioningError as exc:
            if exc.code != "birth_author_keystore_unavailable":
                raise
        else:
            return inspected

        private_raw, public_ring = _legacy_author_material(legacy_keys_dir)
        config, active_id = _expected_store(private_raw, public_ring)

        for stage in _stage_candidates(birth_root):
            try:
                matches = _store_matches(
                    stage, private_raw=private_raw, public_ring=public_ring,
                )
            except BirthKeyStoreError:
                matches = False
            if not matches:
                _remove_stage(stage)
                continue
            try:
                _install_directory(stage, target)
                _sync_directory(birth_root)
            except OSError as exc:
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unavailable", "recovery install",
                ) from exc
            if not _store_matches(
                target, private_raw=private_raw, public_ring=public_ring,
            ):
                raise BirthAuthorityProvisioningError(
                    "birth_author_keystore_existing_invalid",
                )
            return {
                "active_key_id": active_id,
                "created": True,
                "verifiers": len(public_ring),
            }

        temporary = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=birth_root))
        installed = False
        try:
            _build_stage(
                temporary,
                config=config,
                active_id=active_id,
                private_raw=private_raw,
                public_ring=public_ring,
            )
            if not _store_matches(
                temporary, private_raw=private_raw, public_ring=public_ring,
            ):
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unsafe", "staging validation",
                )
            if target.exists() or target.is_symlink():
                raise BirthAuthorityProvisioningError(
                    "birth_author_keystore_conflict", "target appeared",
                )
            try:
                _install_directory(temporary, target)
            except OSError as exc:
                raise BirthAuthorityProvisioningError(
                    "birth_author_provisioning_unavailable", "install",
                ) from exc
            installed = True
            _sync_directory(birth_root)
            if not _store_matches(
                target, private_raw=private_raw, public_ring=public_ring,
            ):
                raise BirthAuthorityProvisioningError(
                    "birth_author_keystore_existing_invalid",
                )
            return {
                "active_key_id": active_id,
                "created": True,
                "verifiers": len(public_ring),
            }
        finally:
            if not installed and temporary.exists():
                _remove_stage(temporary)


__all__ = [
    "BirthAuthorityProvisioningError",
    "inspect_author_keystore",
    "provision_author_keystore",
]
