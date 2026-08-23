"""Encrypted, actor-bound blobs for undo receipts containing secrets.

The append-only undo journal stores only opaque handles.  Secret state is
encrypted at rest with a key derived from the local Metnos admin key, has the
same retention horizon as the undo journal, and can be opened or deleted only
with the original namespace and actor binding.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import config as _C


BLOB_DIR = _C.PATH_USER_DATA / "undo_blobs"
ADMIN_KEY_PATH = _C.PATH_USER_CONFIG / "admin.key"
_HANDLE = re.compile(r"^[0-9a-f]{64}$")
_FORMAT = 1


def retention_days() -> int:
    value = int(os.environ.get("METNOS_UNDO_RETENTION_DAYS", "30"))
    if value < 1 or value > 3650:
        raise ValueError("undo retention days out of range")
    return value


def _master_key() -> bytes:
    raw = ADMIN_KEY_PATH.read_text(encoding="utf-8").strip()
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


def _fernet(salt: bytes) -> Fernet:
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"metnos-protected-undo-v1",
    ).derive(_master_key())
    return Fernet(base64.urlsafe_b64encode(key))


def _validate_binding(owner: str, namespace: str) -> None:
    if not isinstance(owner, str) or not owner or len(owner) > 256:
        raise ValueError("invalid undo blob owner")
    if (not isinstance(namespace, str) or not namespace
            or len(namespace) > 128
            or not all(char.isalnum() or char in "._-" for char in namespace)):
        raise ValueError("invalid undo blob namespace")


def _path(handle: str) -> Path:
    if not isinstance(handle, str) or not _HANDLE.fullmatch(handle):
        raise ValueError("invalid undo blob handle")
    return BLOB_DIR / f"{handle}.age"


def store(data: bytes, *, owner: str, namespace: str) -> str:
    """Encrypt bytes and return the only identifier safe for the journal."""
    if not isinstance(data, bytes):
        raise TypeError("protected undo data must be bytes")
    _validate_binding(owner, namespace)
    created_at = int(time.time())
    envelope = json.dumps({
        "format": _FORMAT,
        "owner": owner,
        "namespace": namespace,
        "created_at": created_at,
        "expires_at": created_at + retention_days() * 86400,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": base64.b64encode(data).decode("ascii"),
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    salt = secrets.token_bytes(16)
    blob = base64.urlsafe_b64encode(salt) + b"\n" + _fernet(salt).encrypt(envelope)
    BLOB_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BLOB_DIR, 0o700)
    for _ in range(4):
        handle = secrets.token_hex(32)
        path = _path(handle)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            remaining = memoryview(blob)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("protected undo blob write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        except Exception:
            os.close(fd)
            path.unlink(missing_ok=True)
            raise
        else:
            os.close(fd)
        try:
            directory_fd = os.open(BLOB_DIR, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return handle
    raise OSError("could not allocate protected undo handle")


def _open(handle: str, *, owner: str, namespace: str,
          allow_expired: bool = False) -> tuple[Path, dict, bytes]:
    _validate_binding(owner, namespace)
    path = _path(handle)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError("protected undo blob permissions are too broad")
    blob = path.read_bytes()
    try:
        salt_b64, token = blob.split(b"\n", 1)
        salt = base64.urlsafe_b64decode(salt_b64)
        if len(salt) != 16:
            raise ValueError("invalid protected undo salt")
        envelope = json.loads(_fernet(salt).decrypt(token).decode("utf-8"))
        data = base64.b64decode(envelope["data"], validate=True)
    except (ValueError, KeyError, InvalidToken, json.JSONDecodeError) as exc:
        raise ValueError("protected undo blob is invalid") from exc
    if (envelope.get("format") != _FORMAT
            or envelope.get("owner") != owner
            or envelope.get("namespace") != namespace):
        raise PermissionError("protected undo blob binding mismatch")
    if not allow_expired and int(envelope.get("expires_at") or 0) < int(time.time()):
        raise PermissionError("protected undo blob expired")
    if envelope.get("sha256") != hashlib.sha256(data).hexdigest():
        raise ValueError("protected undo blob digest mismatch")
    return path, envelope, data


def load(handle: str, *, owner: str, namespace: str) -> bytes:
    return _open(handle, owner=owner, namespace=namespace)[2]


def discard(handle: str, *, owner: str, namespace: str) -> bool:
    path, _envelope, _data = _open(
        handle, owner=owner, namespace=namespace, allow_expired=True)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def purge_expired(*, now: int | None = None) -> int:
    """Remove only blobs whose authenticated envelope is past retention."""
    if not BLOB_DIR.exists():
        return 0
    current = int(time.time()) if now is None else int(now)
    removed = 0
    for path in BLOB_DIR.glob("*.age"):
        try:
            blob = path.read_bytes()
            salt_b64, token = blob.split(b"\n", 1)
            salt = base64.urlsafe_b64decode(salt_b64)
            if len(salt) != 16:
                continue
            envelope = json.loads(_fernet(salt).decrypt(token).decode("utf-8"))
            if int(envelope.get("expires_at") or 0) < current:
                path.unlink()
                removed += 1
        except (OSError, ValueError, InvalidToken, json.JSONDecodeError):
            # Corrupt or foreign data is not deleted by guesswork.
            continue
    return removed
