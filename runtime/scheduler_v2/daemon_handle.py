"""In-process handle to the active SchedulerDaemon.

Set by the HTTP server at startup (PR6 wires this). Other call sites in the
same process look it up to call kick() after writing. Out-of-process callers
(CLI, separate workers) see None and rely on the daemon picking up the new
schedule at next loop iteration.
"""
from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .daemon import SchedulerDaemon

_handle: "weakref.ref | None" = None


def set_active(daemon: "SchedulerDaemon") -> None:
    """Register the active daemon as a weakref.

    Idempotent: replacing the previous handle is safe; the old daemon is
    not stopped here, just dereferenced.
    """
    global _handle
    _handle = weakref.ref(daemon)


def get_active() -> "SchedulerDaemon | None":
    """Resolve the active daemon, or None if absent / GC'd."""
    if _handle is None:
        return None
    return _handle()


def clear() -> None:
    """Drop the handle. Used by tests and on graceful shutdown."""
    global _handle
    _handle = None
