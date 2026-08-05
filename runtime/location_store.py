"""Owner-scoped storage for locations shared through trusted channels.

The immutable user UUID is the authorization key.  ``actor`` is retained only
as a display/audit label and is never used to retrieve another user's data.
Legacy actor-only records remain unreadable and can be retired explicitly with
``purge_unscoped``.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
import time

import config as _C

DEFAULT_LOG = _C.PATH_USER_DATA / "locations.jsonl"
LOCK_PATH = _C.PATH_USER_DATA / "locations.lock"


@contextmanager
def _store_lock(*, exclusive: bool):
    _C.ensure_private_dir(LOCK_PATH.parent)
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(
            descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_all(descriptor: int, body: bytes) -> None:
    remaining = memoryview(body)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write in location store")
        remaining = remaining[written:]


def _read_bytes_unlocked() -> bytes:
    try:
        descriptor = os.open(DEFAULT_LOG, os.O_RDONLY)
    except FileNotFoundError:
        return b""
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _records(data: bytes):
    for raw_line in data.decode("utf-8", "replace").splitlines():
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            yield record


def record_location(*, owner_user_id: str, actor: str, channel: str,
                    lat: float, lon: float,
                    accuracy: float | None = None,
                    source: str | None = None) -> None:
    """Append one authenticated owner's location with a durable write."""

    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("location owner_user_id is required")
    record = {
        "ts": time.time(),
        "owner_user_id": owner,
        "actor": str(actor or ""),
        "channel": str(channel or ""),
        "lat": float(lat),
        "lon": float(lon),
    }
    if accuracy is not None:
        record["accuracy"] = float(accuracy)
    if source:
        record["source"] = str(source)
    line = (json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            + "\n").encode("utf-8")
    with _store_lock(exclusive=True):
        descriptor = os.open(
            DEFAULT_LOG, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            _write_all(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def get_last_location(*, owner_user_id: str) -> dict | None:
    """Return only the latest location of the exact immutable owner."""

    owner = str(owner_user_id or "").strip()
    if not owner:
        return None
    with _store_lock(exclusive=False):
        data = _read_bytes_unlocked()
    last = None
    for record in _records(data):
        if str(record.get("owner_user_id") or "") == owner:
            last = record
    return last


def _rewrite_excluding(*, owner_user_id: str | None = None,
                       unscoped: bool = False) -> int:
    target_owner = str(owner_user_id or "").strip()
    with _store_lock(exclusive=True):
        data = _read_bytes_unlocked()
        retained: list[dict] = []
        removed = 0
        for record in _records(data):
            remove = bool(
                (target_owner
                 and str(record.get("owner_user_id") or "") == target_owner)
                or (unscoped and not record.get("owner_user_id")))
            if remove:
                removed += 1
            else:
                retained.append(record)
        if not data and not retained:
            return removed
        body = "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            + "\n" for record in retained).encode("utf-8")
        temporary = DEFAULT_LOG.with_name(DEFAULT_LOG.name + ".rewrite.tmp")
        descriptor = os.open(
            temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            _write_all(descriptor, body)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, DEFAULT_LOG)
        return removed


def purge_owner(owner_user_id: str) -> int:
    """Physically remove every location belonging to one owner UUID."""

    owner = str(owner_user_id or "").strip()
    return _rewrite_excluding(owner_user_id=owner) if owner else 0


def purge_unscoped() -> int:
    """Physically retire legacy actor-only records, which are never readable."""

    return _rewrite_excluding(unscoped=True)
