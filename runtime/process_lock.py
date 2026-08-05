"""Single process lock shared by Metnos HTTP daemons."""
from __future__ import annotations

import os
from pathlib import Path

from logging_setup import get_logger

log = get_logger(__name__)


class ProcessLock:
    """Non-blocking POSIX flock held for the lifetime of this object."""

    def __init__(self, path: Path, owner: str = "metnos"):
        self.path = Path(path)
        self.owner = owner
        self._fh = None

    def acquire(self) -> None:
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                f"{self.owner} gia' in esecuzione (lockfile {self.path})"
            ) from exc
        self._fh = handle
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.close()
        except OSError as exc:
            log.warning("lock release %s failed: %s", self.path, exc)
        finally:
            self._fh = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.release()

