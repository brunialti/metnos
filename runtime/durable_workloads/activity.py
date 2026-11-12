"""Read-only, non-sensitive evidence about in-flight durable execution."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from .migrations import BUSY_TIMEOUT_MS, CURRENT_SCHEMA_VERSION, default_db_path


ACTIVITY_SCHEMA_VERSION = "metnos.durable-activity/1"


def _unknown(reason_code: str) -> dict[str, object]:
    return {
        "schema_version": ACTIVITY_SCHEMA_VERSION,
        "known": False,
        "reason_code": reason_code,
        "active_attempts": 0,
        "leased_attempts": 0,
        "running_attempts": 0,
    }


def _read_only(path: str | Path | None) -> sqlite3.Connection | str:
    """A read-only connection to the durable store, or why there is none."""
    database_path = Path(default_db_path() if path is None else path).expanduser()
    try:
        metadata = database_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return "database_unsafe"
        resolved = database_path.resolve(strict=True)
        uri = f"file:{quote(os.fspath(resolved), safe='/')}?mode=ro&cache=private"
        return sqlite3.connect(
            uri,
            uri=True,
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
    except (OSError, sqlite3.Error, ValueError):
        return "database_unavailable"


def activity_snapshot(*, path: str | Path | None = None) -> dict[str, object]:
    """Observe authoritative active fences without migrating or writing the DB.

    This is used only as a restart interlock.  It intentionally exposes counts,
    never owners, workload identifiers, inputs, paths or result contents.
    """

    connection = _read_only(path)
    if isinstance(connection, str):
        return _unknown(connection)

    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA query_only=ON")
        version_row = connection.execute(
            "SELECT version FROM durable_schema WHERE singleton=1"
        ).fetchone()
        if version_row is None or int(version_row["version"]) != CURRENT_SCHEMA_VERSION:
            return _unknown("schema_incompatible")
        rows = connection.execute(
            """
            SELECT attempt.state AS state, COUNT(*) AS count
            FROM units AS unit
            JOIN attempts AS attempt
              ON attempt.owner_user_id=unit.owner_user_id
             AND attempt.unit_id=unit.id
             AND attempt.id=unit.active_attempt_id
             AND attempt.fence=unit.fence
             AND attempt.worker_id=unit.lease_worker_id
            WHERE unit.state IN ('leased', 'running')
              AND attempt.state=unit.state
              AND attempt.ended_at IS NULL
            GROUP BY attempt.state
            """
        ).fetchall()
    except (sqlite3.Error, TypeError, ValueError):
        return _unknown("database_unavailable")
    finally:
        connection.close()

    counts = {str(row["state"]): int(row["count"]) for row in rows}
    leased = counts.get("leased", 0)
    running = counts.get("running", 0)
    return {
        "schema_version": ACTIVITY_SCHEMA_VERSION,
        "known": True,
        "reason_code": "none",
        "active_attempts": leased + running,
        "leased_attempts": leased,
        "running_attempts": running,
    }


def long_job_working(*, path: str | Path | None = None) -> bool | None:
    """Whether a durable job is being carried out now; None when unknown.

    A job is at work from its first unit to its end, also in the instants
    between two units, when no attempt is open. Background work that would
    compete for the same models asks here before it starts.
    """

    connection = _read_only(path)
    if isinstance(connection, str):
        return None
    try:
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT 1 FROM workloads WHERE state IN "
            "('running', 'pause_requested', 'cancel_requested') LIMIT 1"
        ).fetchone()
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        connection.close()
    return row is not None


__all__ = ["ACTIVITY_SCHEMA_VERSION", "activity_snapshot", "long_job_working"]
