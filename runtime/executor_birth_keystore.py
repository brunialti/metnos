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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Iterator, Mapping

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
    try:
        info = path.lstat()
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", str(path)) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise BirthKeyStoreError("birth_keystore_unsafe", f"not a real directory: {path}")
    if os.name == "posix":
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            raise BirthKeyStoreError("birth_keystore_unsafe", f"directory permissions: {path}")


def _open_checked(path: Path, *, expected_mode: int = 0o600, writable: bool = False) -> int:
    flags = (os.O_RDWR if writable else os.O_RDONLY)
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", str(path)) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
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
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
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
    if os.name == "nt":
        _check_directory(root)

        def read(relative: str, limit: int) -> bytes:
            return _read_checked(root / relative, limit=limit)

        def check_inventory(public_files: set[str], private_file: str) -> None:
            _closed_inventory(
                root, public_files=public_files, private_file=private_file
            )

        with _store_lock(root / LOCK_BASENAME):
            return _decode_birth_keystore(read, check_inventory, forbidden)
    return _load_birth_keystore_below(root, forbidden)


def _load_birth_keystore_below(root: Path, forbidden) -> LoadedBirthKeyStore:
    """Read one store with the store root as the only absolute name.

    The root is opened once and every name below it, the lock included, is
    opened relative to that descriptor.  A component substituted after the
    anchor therefore cannot redirect a read, and the store that is validated
    is the store the caller named.
    """
    from executor_birth_secure_fs import (
        BirthSecureFSError,
        _BirthObjectRole,
        _open_posix_child_directory,
        _open_posix_directory_root,
        _read_posix_relative,
        _verify_posix_directory,
    )

    private = _BirthObjectRole.historical_private
    try:
        anchor = _open_posix_directory_root(os.fspath(root))
    except BirthSecureFSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", str(root)) from exc
    subdirectories: dict[str, int] = {}
    try:
        try:
            _verify_posix_directory(anchor, role=private, expected_uid=os.geteuid())
            for name in ("private", "public"):
                subdirectories[name] = _open_posix_child_directory(anchor, name)
            _verify_posix_directory(
                subdirectories["private"], role=private, expected_uid=os.geteuid()
            )
            _verify_posix_directory(
                subdirectories["public"], role=private, expected_uid=os.geteuid()
            )
        except BirthSecureFSError as exc:
            raise BirthKeyStoreError(
                "birth_keystore_unsafe", f"directory permissions: {root}"
            ) from exc

        def read(relative: str, limit: int) -> bytes:
            components = PurePosixPath(relative).parts
            if not components or any(
                part in {"", ".", ".."} for part in components
            ):
                raise BirthKeyStoreError("birth_keystore_unsafe", relative)
            directory = anchor
            for part in components[:-1]:
                directory = subdirectories.get(part)
                if directory is None:
                    raise BirthKeyStoreError("birth_keystore_unsafe", relative)
            try:
                return _read_posix_relative(
                    directory,
                    components[-1],
                    maximum=limit,
                    # Section 16.13.4 gives the whole historical key store one
                    # constant profile: even the public keys of this store are
                    # read with the private one, unlike the semantic authority.
                    role=_BirthObjectRole.historical_private,
                    expected_uid=os.geteuid(),
                )
            except BirthSecureFSError as exc:
                raise BirthKeyStoreError(
                    "birth_keystore_unsafe", relative
                ) from exc
            except OSError as exc:
                raise BirthKeyStoreError(
                    "birth_keystore_unavailable", relative
                ) from exc

        def check_inventory(public_files: set[str], private_file: str) -> None:
            try:
                observed = {
                    "": set(os.listdir(anchor)),
                    "public": set(os.listdir(subdirectories["public"])),
                    "private": set(os.listdir(subdirectories["private"])),
                }
            except OSError as exc:
                raise BirthKeyStoreError(
                    "birth_keystore_unavailable", "inventory"
                ) from exc
            if observed[""] != {CONFIG_BASENAME, LOCK_BASENAME, "private", "public"}:
                raise BirthKeyStoreError(
                    "birth_keystore_unsafe", "undeclared root entry"
                )
            if observed["public"] != {
                PurePosixPath(item).name for item in public_files
            }:
                raise BirthKeyStoreError(
                    "birth_keystore_unsafe", "public inventory mismatch"
                )
            if observed["private"] != {PurePosixPath(private_file).name}:
                raise BirthKeyStoreError(
                    "birth_keystore_unsafe", "private inventory mismatch"
                )

        with _store_lock_below(anchor):
            return _decode_birth_keystore(read, check_inventory, forbidden)
    finally:
        for handle in subdirectories.values():
            os.close(handle)
        os.close(anchor)


@contextlib.contextmanager
def _store_lock_below(anchor: int) -> Iterator[None]:
    """Take the shared store lock through the authenticated root descriptor."""
    import fcntl

    from executor_birth_secure_fs import (
        BirthSecureFSError,
        _BirthObjectRole,
        _verify_posix_file,
    )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(LOCK_BASENAME, flags, dir_fd=anchor)
    except OSError as exc:
        raise BirthKeyStoreError("birth_keystore_unavailable", LOCK_BASENAME) from exc
    try:
        try:
            _verify_posix_file(
                fd,
                role=_BirthObjectRole.historical_private,
                expected_uid=os.geteuid(),
            )
        except BirthSecureFSError as exc:
            raise BirthKeyStoreError("birth_keystore_unsafe", LOCK_BASENAME) from exc
        fcntl.flock(fd, fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _load_birth_keystore_in_session(
    directory: tuple[str, ...],
    session,
    *,
    forbidden_public_keys: Iterable[bytes | Ed25519PublicKey] = (),
) -> LoadedBirthKeyStore:
    """Load the store through a session that already holds the global lock.

    Section 16.13.3 fixes this entry.  It never releases or reacquires the
    global lock and it still takes its own shared local lock, because the key
    store is the only store that owns one.  Every byte is read relative to the
    authenticated root, so no name is ever reopened by path.
    """
    from executor_birth_secure_fs import BirthSecureFSError, _BirthObjectRole

    if not session._holds_global_lock():
        raise BirthSecureFSError("birth_provisioning_lock_unsafe")
    base = tuple(directory)
    forbidden = _forbidden_raw(forbidden_public_keys)

    def read(relative: str, limit: int) -> bytes:
        components = base + tuple(PurePosixPath(relative).parts)
        role = (
            _BirthObjectRole.birth_integrity_only
            if relative.startswith("public/")
            else _BirthObjectRole.birth_confidential
        )
        return session.read_file(components, maximum=limit, role=role)

    def check_inventory(public_files: set[str], private_file: str) -> None:
        observed = set(session.inventory(base))
        expected = {CONFIG_BASENAME, LOCK_BASENAME, "private", "public"}
        if observed != expected:
            raise BirthKeyStoreError("birth_keystore_inventory_unexpected", "root")
        if set(session.inventory(base + ("public",))) != {
            PurePosixPath(item).name for item in public_files
        }:
            raise BirthKeyStoreError("birth_keystore_inventory_unexpected", "public")
        if set(session.inventory(base + ("private",))) != {
            PurePosixPath(private_file).name
        }:
            raise BirthKeyStoreError("birth_keystore_inventory_unexpected", "private")

    with session.local_lock(base, exclusive=False, create=False):
        return _decode_birth_keystore(read, check_inventory, forbidden)


def _decode_birth_keystore(read, check_inventory, forbidden) -> LoadedBirthKeyStore:
    """Validate one store from bytes, whatever handle produced them."""
    if True:
        encoded = read(CONFIG_BASENAME, 64 * 1024)
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
            raw = read(public_file, 32)
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

        check_inventory(public_files, private_file)

        private_raw = read(private_file, 32)
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
