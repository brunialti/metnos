"""Owner-scoped storage for locations shared through trusted channels.

The immutable user UUID is the authorization key.  ``actor`` is retained only
as a display/audit label and is never used to retrieve another user's data.
Legacy actor-only records remain unreadable and can be retired explicitly with
``purge_unscoped``.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
import time

import config as _C
from audit_jsonl import jsonl_line, read_jsonl_records

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


def _records_unlocked() -> list[dict] | None:
    """Return the stored records, or None when the store does not exist."""
    try:
        return read_jsonl_records(DEFAULT_LOG)
    except FileNotFoundError:
        return None


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
    line = jsonl_line(record, separators=(",", ":"))
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
        records = _records_unlocked() or []
    last = None
    for record in records:
        if str(record.get("owner_user_id") or "") == owner:
            last = record
    return last


def _rewrite_excluding(*, owner_user_id: str | None = None,
                       unscoped: bool = False) -> int:
    target_owner = str(owner_user_id or "").strip()
    with _store_lock(exclusive=True):
        records = _records_unlocked()
        if records is None:
            return 0
        retained: list[dict] = []
        removed = 0
        for record in records:
            remove = bool(
                (target_owner
                 and str(record.get("owner_user_id") or "") == target_owner)
                or (unscoped and not record.get("owner_user_id")))
            if remove:
                removed += 1
            else:
                retained.append(record)
        body = b"".join(
            jsonl_line(record, separators=(",", ":")) for record in retained)
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
