"""Additive SQLite schema and explicit connection lifecycle.

No connection is opened at import time.  Callers own every returned
connection and decide when migrations are allowed to run.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Final


CURRENT_SCHEMA_VERSION: Final[int] = 1
BUSY_TIMEOUT_MS: Final[int] = 5_000


class MigrationError(RuntimeError):
    """The database schema is absent, malformed or could not be migrated."""


class SchemaTooNewError(MigrationError):
    """The on-disk schema was written by newer code and must not be changed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def default_db_path() -> Path:
    # Lazy import preserves the package's zero-I/O import boundary and lets
    # isolated tests set METNOS_USER_STATE before config is materialized.
    from config import DB_DURABLE_WORKLOADS

    return Path(DB_DURABLE_WORKLOADS)


def _is_memory_path(path: str | Path) -> bool:
    return str(path) == ":memory:"


def _secure_path(path: Path) -> None:
    if path.exists():
        path.chmod(0o600)
    for suffix in ("-wal", "-shm"):
        auxiliary = Path(f"{path}{suffix}")
        if auxiliary.exists():
            auxiliary.chmod(0o600)


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Set WAL even when two first-openers race on a brand-new file.

    SQLite's busy handler is not consistently consulted by the
    `journal_mode` pragma.  A bounded retry covers only that one bootstrap
    race; ordinary statements continue to use SQLite's configured timeout.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            mode = str(
                connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            ).lower()
            if mode != "wal":
                raise MigrationError(f"SQLite refused WAL mode: {mode}")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def open_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Open one configured connection without migrating it.

    File-backed databases use a private 0700 parent and 0600 database.  WAL,
    foreign keys and the busy timeout are configured on every connection.
    `:memory:` is supported for unit tests; SQLite necessarily reports its
    journal mode as `memory` there.
    """
    resolved: str | Path = default_db_path() if path is None else path
    memory = _is_memory_path(resolved)
    file_path: Path | None = None
    if not memory:
        file_path = Path(resolved).expanduser().resolve()
        parent_existed = file_path.parent.exists()
        file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_mode = stat.S_IMODE(file_path.parent.stat().st_mode)
        if parent_existed and parent_mode != 0o700:
            raise MigrationError(
                "durable database parent must already be private (mode 0700)"
            )
        file_path.parent.chmod(0o700)
        try:
            descriptor = os.open(
                file_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        file_path.chmod(0o600)
        resolved = file_path

    connection = sqlite3.connect(
        str(resolved),
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if not memory:
            _enable_wal(connection)
        connection.execute("PRAGMA synchronous=NORMAL")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise MigrationError("SQLite foreign_keys pragma is not active")
        if int(connection.execute("PRAGMA busy_timeout").fetchone()[0]) != BUSY_TIMEOUT_MS:
            raise MigrationError("SQLite busy_timeout pragma is not active")
        if file_path is not None:
            _secure_path(file_path)
        return connection
    except Exception:
        connection.close()
        raise


def schema_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='durable_schema'"
    ).fetchone()
    if table is None:
        return 0
    rows = connection.execute(
        "SELECT singleton, version FROM durable_schema"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        raise MigrationError("durable_schema must contain exactly one version row")
    version = rows[0][1]
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise MigrationError("durable_schema contains an invalid version")
    return version


_V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE durable_schema (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL CHECK (version >= 0),
        applied_at TEXT NOT NULL CHECK (applied_at LIKE '____-__-__T__:__:__%Z')
    )
    """,
    """
    CREATE TABLE workloads (
        owner_user_id TEXT NOT NULL CHECK (
            length(owner_user_id) BETWEEN 1 AND 160
            AND owner_user_id = trim(owner_user_id)
        ),
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        request_key TEXT NOT NULL CHECK (length(request_key) BETWEEN 1 AND 256),
        request_digest TEXT NOT NULL CHECK (
            length(request_digest) = 71
            AND substr(request_digest, 1, 7) = 'sha256:'
            AND substr(request_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        redacted_request_json TEXT NOT NULL CHECK (
            length(CAST(redacted_request_json AS BLOB)) <= 1048576
            AND json_valid(redacted_request_json)
            AND json_type(redacted_request_json) = 'object'
            AND json_extract(redacted_request_json, '$.schema_version') = 'metnos.redacted-request/1'
        ),
        state TEXT NOT NULL CHECK (state IN (
            'draft', 'admitted', 'queued', 'running', 'pause_requested',
            'paused', 'cancel_requested', 'cancelled', 'needs_attention',
            'failed', 'completed_with_errors', 'completed'
        )),
        priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high')),
        budget_json TEXT NOT NULL CHECK (
            length(CAST(budget_json AS BLOB)) <= 1048576
            AND json_valid(budget_json) AND json_type(budget_json) = 'object'
            AND json_extract(budget_json, '$.schema_version') = 'metnos.durable-draft-budget/1'
        ),
        active_revision_id TEXT,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
        next_event_id INTEGER NOT NULL DEFAULT 1 CHECK (next_event_id >= 1),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
        terminal_reason_json TEXT CHECK (
            terminal_reason_json IS NULL OR (
                length(CAST(terminal_reason_json AS BLOB)) <= 65536
                AND json_valid(terminal_reason_json)
                AND json_type(terminal_reason_json) = 'object'
            )
        ),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, request_key)
    ) WITHOUT ROWID
    """,
    "CREATE INDEX workloads_owner_state_idx ON workloads(owner_user_id, state, updated_at DESC)",
    """
    CREATE TABLE revisions (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        workload_id TEXT NOT NULL,
        number INTEGER NOT NULL CHECK (number >= 1),
        plan_schema_version TEXT NOT NULL CHECK (plan_schema_version = 'metnos.durable-plan/1'),
        plan_json TEXT NOT NULL CHECK (
            length(CAST(plan_json AS BLOB)) <= 1048576
            AND json_valid(plan_json) AND json_type(plan_json) = 'object'
        ),
        plan_digest TEXT NOT NULL CHECK (
            length(plan_digest) = 71
            AND substr(plan_digest, 1, 7) = 'sha256:'
            AND substr(plan_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        catalog_snapshot_json TEXT NOT NULL CHECK (
            length(CAST(catalog_snapshot_json AS BLOB)) <= 4194304
            AND json_valid(catalog_snapshot_json)
            AND json_type(catalog_snapshot_json) = 'object'
            AND json_extract(catalog_snapshot_json, '$.schema_version') = 'metnos.catalog-snapshot/1'
        ),
        policy_snapshot_json TEXT NOT NULL CHECK (
            length(CAST(policy_snapshot_json AS BLOB)) <= 4194304
            AND json_valid(policy_snapshot_json)
            AND json_type(policy_snapshot_json) = 'object'
            AND json_extract(policy_snapshot_json, '$.schema_version') = 'metnos.policy-snapshot/1'
        ),
        supersedes_revision_id TEXT,
        inventory_json TEXT CHECK (
            inventory_json IS NULL OR (
                length(CAST(inventory_json AS BLOB)) <= 16777216
                AND json_valid(inventory_json)
                AND json_type(inventory_json) = 'object'
            )
        ),
        inventory_digest TEXT CHECK (
            inventory_digest IS NULL OR (
                length(inventory_digest) = 71
                AND substr(inventory_digest, 1, 7) = 'sha256:'
                AND substr(inventory_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
        ),
        inventory_sealed INTEGER NOT NULL DEFAULT 0 CHECK (inventory_sealed IN (0, 1)),
        expected_source_count INTEGER NOT NULL DEFAULT 0 CHECK (expected_source_count >= 0),
        caps_truncated INTEGER NOT NULL DEFAULT 0 CHECK (caps_truncated IN (0, 1)),
        partial_output_accepted INTEGER NOT NULL DEFAULT 0 CHECK (partial_output_accepted IN (0, 1)),
        usage_complete INTEGER NOT NULL DEFAULT 0 CHECK (usage_complete IN (0, 1)),
        failure_policy TEXT NOT NULL CHECK (failure_policy IN ('strict', 'declared')),
        tolerated_error_classes_json TEXT NOT NULL CHECK (
            length(CAST(tolerated_error_classes_json AS BLOB)) <= 65536
            AND json_valid(tolerated_error_classes_json)
            AND json_type(tolerated_error_classes_json) = 'array'
        ),
        required_artifacts_json TEXT NOT NULL CHECK (
            length(CAST(required_artifacts_json AS BLOB)) <= 65536
            AND json_valid(required_artifacts_json)
            AND json_type(required_artifacts_json) = 'array'
        ),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        admitted_at TEXT CHECK (
            admitted_at IS NULL OR admitted_at LIKE '____-__-__T__:__:__%Z'
        ),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, id, workload_id),
        UNIQUE (owner_user_id, workload_id, number),
        UNIQUE (owner_user_id, workload_id, plan_digest, inventory_digest),
        FOREIGN KEY (owner_user_id, workload_id)
            REFERENCES workloads(owner_user_id, id) ON DELETE CASCADE,
        FOREIGN KEY (owner_user_id, supersedes_revision_id, workload_id)
            REFERENCES revisions(owner_user_id, id, workload_id)
            DEFERRABLE INITIALLY DEFERRED
    ) WITHOUT ROWID
    """,
    "CREATE INDEX revisions_workload_idx ON revisions(owner_user_id, workload_id, number DESC)",
    """
    CREATE TRIGGER revisions_admitted_immutable
    BEFORE UPDATE ON revisions
    WHEN OLD.admitted_at IS NOT NULL AND (
        NEW.owner_user_id IS NOT OLD.owner_user_id
        OR NEW.id IS NOT OLD.id
        OR NEW.workload_id IS NOT OLD.workload_id
        OR NEW.number IS NOT OLD.number
        OR NEW.plan_schema_version IS NOT OLD.plan_schema_version
        OR NEW.plan_json IS NOT OLD.plan_json
        OR NEW.plan_digest IS NOT OLD.plan_digest
        OR NEW.catalog_snapshot_json IS NOT OLD.catalog_snapshot_json
        OR NEW.policy_snapshot_json IS NOT OLD.policy_snapshot_json
        OR NEW.supersedes_revision_id IS NOT OLD.supersedes_revision_id
        OR NEW.inventory_json IS NOT OLD.inventory_json
        OR NEW.inventory_digest IS NOT OLD.inventory_digest
        OR NEW.inventory_sealed IS NOT OLD.inventory_sealed
        OR NEW.expected_source_count IS NOT OLD.expected_source_count
        OR NEW.partial_output_accepted IS NOT OLD.partial_output_accepted
        OR NEW.failure_policy IS NOT OLD.failure_policy
        OR NEW.tolerated_error_classes_json IS NOT OLD.tolerated_error_classes_json
        OR NEW.required_artifacts_json IS NOT OLD.required_artifacts_json
        OR NEW.created_at IS NOT OLD.created_at
        OR NEW.admitted_at IS NOT OLD.admitted_at
    )
    BEGIN
        SELECT RAISE(ABORT, 'admitted revision is immutable');
    END
    """,
    """
    CREATE TRIGGER workloads_active_revision_owner_guard
    BEFORE UPDATE OF active_revision_id ON workloads
    WHEN NEW.active_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM revisions r
        WHERE r.owner_user_id = NEW.owner_user_id
          AND r.id = NEW.active_revision_id
          AND r.workload_id = NEW.id
    )
    BEGIN
        SELECT RAISE(ABORT, 'active revision does not belong to workload owner');
    END
    """,
    """
    CREATE TABLE stages (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        revision_id TEXT NOT NULL,
        stage_key TEXT NOT NULL CHECK (length(stage_key) BETWEEN 1 AND 64),
        position INTEGER NOT NULL CHECK (position >= 0),
        stage_type TEXT NOT NULL CHECK (stage_type IN ('inventory', 'map', 'reduce', 'validate', 'publish')),
        runner_kind TEXT NOT NULL CHECK (runner_kind IN ('internal', 'executor', 'workload')),
        runner_name TEXT NOT NULL CHECK (length(runner_name) BETWEEN 2 AND 96),
        effect_profile TEXT NOT NULL CHECK (effect_profile IN ('pure', 'idempotent', 'reconcilable', 'manual_only')),
        cardinality TEXT NOT NULL CHECK (cardinality IN ('singleton', 'per_source', 'per_dependency')),
        max_units INTEGER NOT NULL CHECK (max_units >= 1),
        input_bindings_json TEXT NOT NULL CHECK (json_valid(input_bindings_json) AND json_type(input_bindings_json) = 'object'),
        output_schema_json TEXT NOT NULL CHECK (json_valid(output_schema_json) AND json_type(output_schema_json) = 'object'),
        retry_json TEXT NOT NULL CHECK (json_valid(retry_json) AND json_type(retry_json) = 'object'),
        timeout_s INTEGER NOT NULL CHECK (timeout_s BETWEEN 1 AND 86400),
        invalidation_json TEXT NOT NULL CHECK (json_valid(invalidation_json) AND json_type(invalidation_json) = 'array'),
        resources_json TEXT NOT NULL CHECK (json_valid(resources_json) AND json_type(resources_json) = 'object'),
        required_flag INTEGER NOT NULL CHECK (required_flag IN (0, 1)),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, revision_id, stage_key),
        UNIQUE (owner_user_id, revision_id, position),
        UNIQUE (owner_user_id, id, revision_id),
        FOREIGN KEY (owner_user_id, revision_id)
            REFERENCES revisions(owner_user_id, id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE stage_dependencies (
        owner_user_id TEXT NOT NULL,
        revision_id TEXT NOT NULL,
        stage_id TEXT NOT NULL,
        depends_on_stage_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        PRIMARY KEY (owner_user_id, revision_id, stage_id, depends_on_stage_id),
        UNIQUE (owner_user_id, revision_id, stage_id, ordinal),
        CHECK (stage_id <> depends_on_stage_id),
        FOREIGN KEY (owner_user_id, stage_id, revision_id)
            REFERENCES stages(owner_user_id, id, revision_id) ON DELETE CASCADE,
        FOREIGN KEY (owner_user_id, depends_on_stage_id, revision_id)
            REFERENCES stages(owner_user_id, id, revision_id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE sources (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 160),
        revision_id TEXT NOT NULL,
        source_id TEXT NOT NULL CHECK (length(source_id) BETWEEN 8 AND 160),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        device_id TEXT NOT NULL CHECK (length(device_id) BETWEEN 1 AND 128),
        locator_redacted TEXT NOT NULL CHECK (length(locator_redacted) BETWEEN 1 AND 1024),
        kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 64),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        mtime_ns INTEGER NOT NULL CHECK (mtime_ns >= 0),
        content_digest TEXT NOT NULL CHECK (
            length(content_digest) = 71
            AND substr(content_digest, 1, 7) = 'sha256:'
            AND substr(content_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        state TEXT NOT NULL CHECK (state IN ('ready', 'unstable', 'missing', 'skipped')),
        accounted INTEGER NOT NULL CHECK (accounted IN (0, 1)),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, revision_id, source_id),
        UNIQUE (owner_user_id, revision_id, ordinal),
        UNIQUE (owner_user_id, id, revision_id),
        FOREIGN KEY (owner_user_id, revision_id)
            REFERENCES revisions(owner_user_id, id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TRIGGER sources_admitted_identity_immutable
    BEFORE UPDATE OF source_id, ordinal, device_id, locator_redacted, kind,
        size_bytes, mtime_ns, content_digest ON sources
    WHEN EXISTS (
        SELECT 1 FROM revisions r
        WHERE r.owner_user_id = OLD.owner_user_id
          AND r.id = OLD.revision_id
          AND r.admitted_at IS NOT NULL
    )
    BEGIN
        SELECT RAISE(ABORT, 'admitted source identity is immutable');
    END
    """,
    """
    CREATE TABLE units (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        revision_id TEXT NOT NULL,
        stage_id TEXT NOT NULL,
        unit_key TEXT NOT NULL CHECK (length(unit_key) BETWEEN 8 AND 256),
        source_row_id TEXT,
        shard_key TEXT,
        state TEXT NOT NULL CHECK (state IN (
            'pending', 'leased', 'running', 'retry_wait', 'committed',
            'failed_permanent', 'needs_attention', 'cancelled', 'skipped'
        )),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        expected_dependency_count INTEGER NOT NULL DEFAULT 0 CHECK (expected_dependency_count >= 0),
        next_attempt_at TEXT CHECK (next_attempt_at IS NULL OR next_attempt_at LIKE '____-__-__T__:__:__%Z'),
        lease_worker_id TEXT,
        active_attempt_id TEXT,
        fence INTEGER NOT NULL DEFAULT 0 CHECK (fence >= 0),
        lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR lease_expires_at LIKE '____-__-__T__:__:__%Z'),
        committed_result_id TEXT,
        error_class TEXT,
        partial_output INTEGER NOT NULL DEFAULT 0 CHECK (partial_output IN (0, 1)),
        terminal_detail_json TEXT CHECK (
            terminal_detail_json IS NULL OR (
                length(CAST(terminal_detail_json AS BLOB)) <= 65536
                AND json_valid(terminal_detail_json)
                AND json_type(terminal_detail_json) = 'object'
            )
        ),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, revision_id, stage_id, unit_key),
        UNIQUE (owner_user_id, id, revision_id),
        FOREIGN KEY (owner_user_id, revision_id)
            REFERENCES revisions(owner_user_id, id) ON DELETE CASCADE,
        FOREIGN KEY (owner_user_id, stage_id, revision_id)
            REFERENCES stages(owner_user_id, id, revision_id) ON DELETE CASCADE,
        FOREIGN KEY (owner_user_id, source_row_id, revision_id)
            REFERENCES sources(owner_user_id, id, revision_id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    "CREATE INDEX units_ready_idx ON units(owner_user_id, state, next_attempt_at, revision_id, stage_id)",
    """
    CREATE TRIGGER units_fence_monotonic
    BEFORE UPDATE OF fence ON units
    WHEN NEW.fence < OLD.fence
    BEGIN
        SELECT RAISE(ABORT, 'unit fence cannot decrease');
    END
    """,
    """
    CREATE TRIGGER units_attempt_count_monotonic
    BEFORE UPDATE OF attempt_count ON units
    WHEN NEW.attempt_count < OLD.attempt_count
    BEGIN
        SELECT RAISE(ABORT, 'unit attempt_count cannot decrease');
    END
    """,
    """
    CREATE TABLE attempts (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        unit_id TEXT NOT NULL,
        number INTEGER NOT NULL CHECK (number >= 1),
        fence INTEGER NOT NULL CHECK (fence >= 1),
        worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 128),
        device_id TEXT,
        invocation_id TEXT,
        state TEXT NOT NULL CHECK (state IN (
            'leased', 'running', 'succeeded', 'failed', 'timed_out',
            'late_rejected', 'abandoned'
        )),
        started_at TEXT NOT NULL CHECK (started_at LIKE '____-__-__T__:__:__%Z'),
        ended_at TEXT CHECK (ended_at IS NULL OR ended_at LIKE '____-__-__T__:__:__%Z'),
        structured_error_json TEXT CHECK (
            structured_error_json IS NULL OR (
                length(CAST(structured_error_json AS BLOB)) <= 65536
                AND json_valid(structured_error_json)
                AND json_type(structured_error_json) = 'object'
            )
        ),
        executor_snapshot_json TEXT NOT NULL CHECK (
            length(CAST(executor_snapshot_json AS BLOB)) <= 4194304
            AND json_valid(executor_snapshot_json)
            AND json_type(executor_snapshot_json) = 'object'
        ),
        model_snapshot_json TEXT NOT NULL CHECK (
            length(CAST(model_snapshot_json AS BLOB)) <= 4194304
            AND json_valid(model_snapshot_json)
            AND json_type(model_snapshot_json) = 'object'
        ),
        metrics_json TEXT NOT NULL CHECK (
            length(CAST(metrics_json AS BLOB)) <= 1048576
            AND json_valid(metrics_json) AND json_type(metrics_json) = 'object'
        ),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, unit_id, number),
        UNIQUE (owner_user_id, unit_id, fence),
        UNIQUE (owner_user_id, id, unit_id, fence),
        FOREIGN KEY (owner_user_id, unit_id)
            REFERENCES units(owner_user_id, id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TRIGGER attempts_terminal_immutable
    BEFORE UPDATE ON attempts
    WHEN OLD.ended_at IS NOT NULL AND (
        NEW.state IS NOT OLD.state
        OR NEW.ended_at IS NOT OLD.ended_at
        OR NEW.structured_error_json IS NOT OLD.structured_error_json
        OR NEW.executor_snapshot_json IS NOT OLD.executor_snapshot_json
        OR NEW.model_snapshot_json IS NOT OLD.model_snapshot_json
        OR NEW.metrics_json IS NOT OLD.metrics_json
    )
    BEGIN
        SELECT RAISE(ABORT, 'terminal attempt is immutable');
    END
    """,
    """
    CREATE TABLE results (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        revision_id TEXT NOT NULL,
        unit_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        fence INTEGER NOT NULL CHECK (fence >= 1),
        digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        schema_version TEXT NOT NULL CHECK (length(schema_version) BETWEEN 3 AND 160),
        payload_json TEXT CHECK (
            payload_json IS NULL OR (
                length(CAST(payload_json AS BLOB)) <= 8388608
                AND json_valid(payload_json)
            )
        ),
        blob_ref TEXT,
        provenance_json TEXT NOT NULL CHECK (
            length(CAST(provenance_json AS BLOB)) <= 4194304
            AND json_valid(provenance_json)
            AND json_type(provenance_json) = 'object'
        ),
        committed_at TEXT NOT NULL CHECK (committed_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, unit_id),
        UNIQUE (owner_user_id, id, revision_id),
        CHECK (payload_json IS NOT NULL OR blob_ref IS NOT NULL),
        FOREIGN KEY (owner_user_id, revision_id)
            REFERENCES revisions(owner_user_id, id) ON DELETE CASCADE,
        FOREIGN KEY (owner_user_id, unit_id, revision_id)
            REFERENCES units(owner_user_id, id, revision_id) ON DELETE CASCADE,
        FOREIGN KEY (owner_user_id, attempt_id, unit_id, fence)
            REFERENCES attempts(owner_user_id, id, unit_id, fence) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TRIGGER results_immutable
    BEFORE UPDATE ON results
    BEGIN
        SELECT RAISE(ABORT, 'committed result is immutable');
    END
    """,
    """
    CREATE TRIGGER units_active_attempt_owner_guard
    BEFORE UPDATE OF active_attempt_id ON units
    WHEN NEW.active_attempt_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM attempts a
        WHERE a.owner_user_id = NEW.owner_user_id
          AND a.id = NEW.active_attempt_id
          AND a.unit_id = NEW.id
    )
    BEGIN
        SELECT RAISE(ABORT, 'active attempt does not belong to unit owner');
    END
    """,
    """
    CREATE TRIGGER units_committed_result_owner_guard
    BEFORE UPDATE OF committed_result_id ON units
    WHEN NEW.committed_result_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM results r
        WHERE r.owner_user_id = NEW.owner_user_id
          AND r.id = NEW.committed_result_id
          AND r.unit_id = NEW.id
    )
    BEGIN
        SELECT RAISE(ABORT, 'committed result does not belong to unit owner');
    END
    """,
    """
    CREATE TABLE dependencies (
        owner_user_id TEXT NOT NULL,
        revision_id TEXT NOT NULL,
        child_result_id TEXT NOT NULL,
        source_result_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (length(role) BETWEEN 1 AND 64),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        PRIMARY KEY (owner_user_id, revision_id, child_result_id, source_result_id, role),
        UNIQUE (owner_user_id, revision_id, child_result_id, role, ordinal),
        CHECK (child_result_id <> source_result_id),
        FOREIGN KEY (owner_user_id, child_result_id, revision_id)
            REFERENCES results(owner_user_id, id, revision_id) ON DELETE CASCADE,
        FOREIGN KEY (owner_user_id, source_result_id, revision_id)
            REFERENCES results(owner_user_id, id, revision_id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE artifacts (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        workload_id TEXT NOT NULL,
        revision_id TEXT NOT NULL,
        logical_name TEXT NOT NULL CHECK (length(logical_name) BETWEEN 1 AND 64),
        digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        mime_type TEXT NOT NULL CHECK (instr(mime_type, '/') > 1),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        schema_version TEXT NOT NULL CHECK (length(schema_version) BETWEEN 3 AND 160),
        state TEXT NOT NULL CHECK (state IN ('prepared', 'committed', 'published', 'needs_attention', 'expired')),
        blob_ref TEXT NOT NULL CHECK (length(blob_ref) BETWEEN 1 AND 512),
        retention_until TEXT CHECK (retention_until IS NULL OR retention_until LIKE '____-__-__T__:__:__%Z'),
        published_target_redacted TEXT,
        digest_verified INTEGER NOT NULL CHECK (digest_verified IN (0, 1)),
        schema_valid INTEGER NOT NULL CHECK (schema_valid IN (0, 1)),
        postconditions_valid INTEGER NOT NULL CHECK (postconditions_valid IN (0, 1)),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, revision_id, logical_name),
        FOREIGN KEY (owner_user_id, revision_id, workload_id)
            REFERENCES revisions(owner_user_id, id, workload_id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE publications (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        artifact_id TEXT NOT NULL,
        target_key TEXT NOT NULL CHECK (length(target_key) BETWEEN 1 AND 256),
        target_redacted TEXT NOT NULL CHECK (length(target_redacted) BETWEEN 1 AND 1024),
        state TEXT NOT NULL CHECK (state IN ('prepared', 'published', 'needs_attention', 'cancelled')),
        expected_digest TEXT NOT NULL CHECK (
            length(expected_digest) = 71
            AND substr(expected_digest, 1, 7) = 'sha256:'
            AND substr(expected_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        observed_digest TEXT CHECK (
            observed_digest IS NULL OR (
                length(observed_digest) = 71
                AND substr(observed_digest, 1, 7) = 'sha256:'
                AND substr(observed_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
        ),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        prepared_at TEXT NOT NULL CHECK (prepared_at LIKE '____-__-__T__:__:__%Z'),
        published_at TEXT CHECK (published_at IS NULL OR published_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, artifact_id, target_key),
        CHECK (
            state <> 'published' OR (
                observed_digest IS NOT NULL
                AND observed_digest = expected_digest
                AND published_at IS NOT NULL
            )
        ),
        FOREIGN KEY (owner_user_id, artifact_id)
            REFERENCES artifacts(owner_user_id, id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE events (
        owner_user_id TEXT NOT NULL,
        workload_id TEXT NOT NULL,
        event_id INTEGER NOT NULL CHECK (event_id >= 1),
        type TEXT NOT NULL CHECK (type IN (
            'draft_created', 'revision_admitted', 'queued', 'running',
            'pause_requested', 'paused', 'resumed', 'cancel_requested',
            'cancelled', 'needs_attention', 'attention_resolved', 'failed',
            'completed_with_errors', 'completed'
        )),
        payload_json TEXT NOT NULL CHECK (
            length(CAST(payload_json AS BLOB)) <= 65536
            AND json_valid(payload_json) AND json_type(payload_json) = 'object'
        ),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, workload_id, event_id),
        FOREIGN KEY (owner_user_id, workload_id)
            REFERENCES workloads(owner_user_id, id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE outbox (
        owner_user_id TEXT NOT NULL,
        id TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 128),
        workload_id TEXT NOT NULL,
        event_id INTEGER NOT NULL,
        channel TEXT NOT NULL CHECK (channel IN ('owner_event', 'telegram')),
        recipient_key TEXT NOT NULL CHECK (length(recipient_key) BETWEEN 1 AND 256),
        state TEXT NOT NULL CHECK (state IN ('pending', 'leased', 'sent', 'failed', 'cancelled')),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        next_attempt_at TEXT CHECK (next_attempt_at IS NULL OR next_attempt_at LIKE '____-__-__T__:__:__%Z'),
        lease_worker_id TEXT,
        fence INTEGER NOT NULL DEFAULT 0 CHECK (fence >= 0),
        ack_json TEXT CHECK (ack_json IS NULL OR (json_valid(ack_json) AND json_type(ack_json) = 'object')),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, id),
        UNIQUE (owner_user_id, workload_id, event_id, channel, recipient_key),
        FOREIGN KEY (owner_user_id, workload_id, event_id)
            REFERENCES events(owner_user_id, workload_id, event_id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE scheduler_credits (
        owner_user_id TEXT NOT NULL,
        workload_id TEXT NOT NULL,
        deficit INTEGER NOT NULL DEFAULT 0,
        last_selected_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_selected_seq >= 0),
        quota INTEGER NOT NULL DEFAULT 1 CHECK (quota BETWEEN 1 AND 256),
        updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, workload_id),
        FOREIGN KEY (owner_user_id, workload_id)
            REFERENCES workloads(owner_user_id, id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE commands (
        owner_user_id TEXT NOT NULL,
        workload_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
        command TEXT NOT NULL CHECK (command IN ('pause', 'resume', 'cancel', 'resolve_attention')),
        payload_digest TEXT NOT NULL CHECK (
            length(payload_digest) = 71
            AND substr(payload_digest, 1, 7) = 'sha256:'
            AND substr(payload_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        result_json TEXT NOT NULL CHECK (
            length(CAST(result_json AS BLOB)) <= 65536
            AND json_valid(result_json) AND json_type(result_json) = 'object'
            AND json_extract(result_json, '$.schema_version') = 'metnos.durable-command-result/1'
        ),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, workload_id, idempotency_key),
        FOREIGN KEY (owner_user_id, workload_id)
            REFERENCES workloads(owner_user_id, id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE attention_resolutions (
        owner_user_id TEXT NOT NULL,
        workload_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('retry', 'cancel')),
        note_redacted TEXT CHECK (note_redacted IS NULL OR length(note_redacted) <= 2048),
        created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
        PRIMARY KEY (owner_user_id, workload_id, idempotency_key),
        FOREIGN KEY (owner_user_id, workload_id, idempotency_key)
            REFERENCES commands(owner_user_id, workload_id, idempotency_key) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TRIGGER workloads_terminal_event_guard
    BEFORE UPDATE OF state ON workloads
    WHEN NEW.state IN ('completed', 'completed_with_errors') AND NOT EXISTS (
        SELECT 1
        FROM events e
        JOIN outbox o
          ON o.owner_user_id = e.owner_user_id
         AND o.workload_id = e.workload_id
         AND o.event_id = e.event_id
        WHERE e.owner_user_id = NEW.owner_user_id
          AND e.workload_id = NEW.id
          AND e.type = NEW.state
          AND o.channel = 'owner_event'
    )
    BEGIN
        SELECT RAISE(ABORT, 'terminal completion requires event and outbox');
    END
    """,
    "INSERT INTO durable_schema(singleton, version, applied_at) VALUES (1, 1, '__APPLIED_AT__')",
)


_REQUIRED_V1_TABLES = frozenset({
    "durable_schema",
    "workloads",
    "revisions",
    "stages",
    "stage_dependencies",
    "sources",
    "units",
    "attempts",
    "results",
    "dependencies",
    "artifacts",
    "publications",
    "events",
    "outbox",
    "scheduler_credits",
    "commands",
    "attention_resolutions",
})


def _validate_schema_shape(connection: sqlite3.Connection, version: int) -> None:
    if version != 1:
        return
    present = frozenset(
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    )
    missing = sorted(_REQUIRED_V1_TABLES - present)
    if missing:
        raise MigrationError(f"schema v1 is missing required tables: {missing}")


def migrate(
    connection: sqlite3.Connection,
    *,
    _before_statement: Callable[[int, str], None] | None = None,
) -> int:
    """Apply all known additive migrations atomically and return the version.

    `_before_statement` is an internal fault-injection seam used to prove that
    the version and partial tables roll back together.
    """
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = schema_version(connection)
        if current > CURRENT_SCHEMA_VERSION:
            raise SchemaTooNewError(
                f"database schema {current} is newer than supported "
                f"{CURRENT_SCHEMA_VERSION}"
            )
        if current == 0:
            applied_at = utc_now()
            for index, raw_statement in enumerate(_V1_STATEMENTS, start=1):
                statement = raw_statement.replace("__APPLIED_AT__", applied_at)
                if _before_statement is not None:
                    _before_statement(index, statement)
                connection.execute(statement)
            current = 1
        _validate_schema_shape(connection, current)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(f"foreign-key violations after migration: {violations[:3]}")
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise

    row = connection.execute("PRAGMA database_list").fetchone()
    if row is not None and row[2] not in {None, "", ":memory:"}:
        _secure_path(Path(row[2]))
    return current


def schema_dump(connection: sqlite3.Connection) -> str:
    """Return a stable, reviewable schema dump without SQLite internals."""
    rows = connection.execute(
        """
        SELECT type, name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
        ORDER BY CASE type
            WHEN 'table' THEN 0 WHEN 'index' THEN 1 WHEN 'trigger' THEN 2 ELSE 3
        END, name
        """
    ).fetchall()
    return "\n\n".join(str(row[2]).strip() + ";" for row in rows) + "\n"


__all__ = [
    "BUSY_TIMEOUT_MS",
    "CURRENT_SCHEMA_VERSION",
    "MigrationError",
    "SchemaTooNewError",
    "default_db_path",
    "migrate",
    "open_db",
    "schema_dump",
    "schema_version",
    "utc_now",
]
