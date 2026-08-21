"""Small transaction boundary shared by the private LRE repositories."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager


Checkpoint = Callable[[str], None]


def noop_checkpoint(_name: str) -> None:
    """Default checkpoint with no runtime side effects."""


def checked_checkpoint(checkpoint: Checkpoint | None) -> Checkpoint:
    if checkpoint is None:
        return noop_checkpoint
    if not callable(checkpoint):
        raise TypeError("checkpoint must be callable")
    return checkpoint


@contextmanager
def immediate_transaction(
    connection: sqlite3.Connection,
    checkpoint: Checkpoint,
    *,
    name: str,
) -> Iterator[sqlite3.Connection]:
    """Run one SQLite writer transaction with deterministic fault boundaries."""

    checkpoint(f"{name}_before_begin")
    connection.execute("BEGIN IMMEDIATE")
    try:
        checkpoint(f"{name}_after_begin")
        yield connection
        checkpoint(f"{name}_before_commit")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            try:
                checkpoint(f"{name}_before_rollback")
            finally:
                connection.execute("ROLLBACK")
            checkpoint(f"{name}_after_rollback")
        raise
    checkpoint(f"{name}_after_commit")


__all__ = [
    "Checkpoint",
    "checked_checkpoint",
    "immediate_transaction",
]
