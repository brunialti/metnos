"""Progress context for long-running in-process executors.

The engine binds the channel-specific ``Progress`` object around one executor
invocation.  Builtins can then publish bounded, data-free checkpoints without
putting callbacks in executor arguments (which would break validation, JSON
logging and remote transports).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_CURRENT_PROGRESS: ContextVar[object | None] = ContextVar(
    "metnos_executor_progress", default=None,
)


@contextmanager
def bind(progress: object | None) -> Iterator[None]:
    token = _CURRENT_PROGRESS.set(progress)
    try:
        yield
    finally:
        _CURRENT_PROGRESS.reset(token)


def update(label: str) -> bool:
    """Publish one free-form checkpoint; return whether it was delivered."""
    progress = _CURRENT_PROGRESS.get()
    callback = getattr(progress, "update_free", None)
    if not callable(callback):
        return False
    try:
        callback(str(label))
        return True
    except Exception:
        return False
