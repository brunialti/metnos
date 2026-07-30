"""Cross-process admission lock for owner-scoped state and deletion."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import fcntl
import hashlib
import os

import config


class OwnerUnavailable(RuntimeError):
    """The logical owner is being deleted or was already deleted."""


def _lock_path(owner_user_id: str):
    digest = hashlib.sha256(
        ("metnos-owner-lifecycle-v1\0" + owner_user_id).encode("utf-8")
    ).hexdigest()
    return config.PATH_USER_STATE / "user_lifecycle" / f"{digest}.lock"


@contextmanager
def owner_session(owner_user_id: str):
    """Hold a shared owner lease and reject durable deletion tombstones."""

    owner = str(owner_user_id or "")
    if not owner:
        raise OwnerUnavailable("missing owner")
    path = _lock_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        import users
        if users.owner_deletion_started(owner):
            raise OwnerUnavailable("owner deletion started")
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@asynccontextmanager
async def async_owner_session(owner_user_id: str):
    """Async shared lease that never blocks the server event-loop thread."""

    owner = str(owner_user_id or "")
    if not owner:
        raise OwnerUnavailable("missing owner")
    path = _lock_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(
                    descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                await asyncio.sleep(0.02)
        import users
        deletion_started = await asyncio.to_thread(
            users.owner_deletion_started, owner)
        if deletion_started:
            raise OwnerUnavailable("owner deletion started")
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def owner_deletion(owner_user_id: str):
    """Exclude all cooperating owner writers for the full purge lifecycle."""

    owner = str(owner_user_id or "")
    if not owner:
        raise ValueError("missing owner")
    path = _lock_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
