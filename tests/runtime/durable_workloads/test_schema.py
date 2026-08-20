from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from durable_workloads.migrations import (
    BUSY_TIMEOUT_MS,
    CURRENT_SCHEMA_VERSION,
    MigrationError,
    SchemaTooNewError,
    migrate,
    open_db,
    schema_dump,
    schema_version,
)
from helpers import inventory, plan, source


@pytest.fixture
def store(tmp_path):
    from durable_workloads.storage import DurableWorkloadStore

    repository = DurableWorkloadStore.open(
        tmp_path / "durable" / "state.sqlite3"
    )
    try:
        yield repository
    finally:
        repository.close()


def test_import_has_no_database_or_directory_side_effect(tmp_path):
    state = tmp_path / "state-not-created"
    environment = dict(os.environ)
    environment["METNOS_USER_STATE"] = str(state)
    environment["PYTHONPATH"] = str(
        __import__("pathlib").Path(__file__).resolve().parents[3] / "runtime"
    )
    subprocess.run(
        [sys.executable, "-c", "import durable_workloads"],
        env=environment,
        check=True,
    )
    assert not state.exists()


def test_empty_database_migrates_with_required_pragmas(tmp_path):
    path = tmp_path / "private" / "state.sqlite3"
    connection = open_db(path)
    try:
        assert schema_version(connection) == 0
        assert migrate(connection) == CURRENT_SCHEMA_VERSION
        assert schema_version(connection) == CURRENT_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        connection.close()


def test_double_migration_is_idempotent_and_dump_is_stable():
    connection = open_db(":memory:")
    try:
        assert migrate(connection) == CURRENT_SCHEMA_VERSION
        first = schema_dump(connection)
        assert migrate(connection) == CURRENT_SCHEMA_VERSION
        assert schema_dump(connection) == first
        assert "CREATE TABLE workloads" in first
        assert "CREATE TRIGGER workloads_terminal_event_guard" in first
    finally:
        connection.close()


def test_migration_rolls_back_tables_and_version_on_injected_error():
    connection = open_db(":memory:")

    def fail(statement_number, _statement):
        if statement_number == 6:
            raise RuntimeError("injected migration fault")

    try:
        with pytest.raises(RuntimeError, match="injected"):
            migrate(connection, _before_statement=fail)
        assert schema_version(connection) == 0
        names = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "workloads" not in names
        assert "durable_schema" not in names
        assert migrate(connection) == CURRENT_SCHEMA_VERSION
    finally:
        connection.close()


def test_future_schema_is_rejected_without_change():
    connection = open_db(":memory:")
    try:
        connection.execute(
            "CREATE TABLE durable_schema(singleton INTEGER PRIMARY KEY, version INTEGER, applied_at TEXT)"
        )
        connection.execute(
            "INSERT INTO durable_schema VALUES (1, ?, '2026-08-20T00:00:00Z')",
            (CURRENT_SCHEMA_VERSION + 1,),
        )
        with pytest.raises(SchemaTooNewError):
            migrate(connection)
        assert schema_version(connection) == CURRENT_SCHEMA_VERSION + 1
    finally:
        connection.close()


def test_file_and_directory_permissions_ignore_process_umask(tmp_path):
    path = tmp_path / "durable-private" / "state.sqlite3"
    previous = os.umask(0)
    try:
        connection = open_db(path)
        migrate(connection)
        connection.close()
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_open_refuses_to_repermission_a_shared_parent(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(MigrationError, match="parent must already be private"):
        open_db(shared / "state.sqlite3")
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755


def test_two_connections_can_migrate_the_same_new_database(tmp_path):
    path = tmp_path / "concurrent" / "state.sqlite3"

    def open_and_migrate():
        connection = open_db(path)
        try:
            return migrate(connection), schema_version(connection)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: open_and_migrate(), range(2)))
    assert results == [(CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION)] * 2


def test_relational_owner_state_foreign_key_and_fence_constraints(store):
    first = store.create_draft(
        "owner-a", "request-a", redacted_request={"summary": "fixture"}
    )
    revision = store.admit_revision(
        "owner-a",
        first.workload_id,
        plan(with_map=True),
        inventory([source(0)]),
        expected_version=first.version,
    )
    connection = store._connection
    unit = connection.execute(
        "SELECT id FROM units WHERE owner_user_id=? AND revision_id=?",
        ("owner-a", revision.revision_id),
    ).fetchone()
    assert unit is not None
    connection.execute(
        "UPDATE units SET fence=4 WHERE owner_user_id=? AND id=?",
        ("owner-a", unit["id"]),
    )
    with pytest.raises(sqlite3.IntegrityError, match="fence"):
        connection.execute(
            "UPDATE units SET fence=3 WHERE owner_user_id=? AND id=?",
            ("owner-a", unit["id"]),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE units SET stage_id='stg_missing' WHERE owner_user_id=? AND id=?",
            ("owner-a", unit["id"]),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE workloads SET state='invented' WHERE owner_user_id=? AND id=?",
            ("owner-a", first.workload_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="revision is immutable"):
        connection.execute(
            "UPDATE revisions SET plan_json='{}' WHERE owner_user_id=? AND id=?",
            ("owner-a", revision.revision_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="revision is immutable"):
        connection.execute(
            """
            UPDATE revisions SET failure_policy='declared'
            WHERE owner_user_id=? AND id=?
            """,
            ("owner-a", revision.revision_id),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            UPDATE workloads SET request_digest=?
            WHERE owner_user_id=? AND id=?
            """,
            ("sha256:" + "a" * 63 + "z", "owner-a", first.workload_id),
        )


def test_artifact_revision_must_belong_to_the_same_workload(store):
    first = store.create_draft(
        "owner-a", "request-artifact-a", redacted_request={"summary": "a"}
    )
    first_revision = store.admit_revision(
        "owner-a", first.workload_id, plan(), inventory(),
        expected_version=first.version,
    )
    second = store.create_draft(
        "owner-a", "request-artifact-b", redacted_request={"summary": "b"}
    )
    second_revision = store.admit_revision(
        "owner-a", second.workload_id, plan(), inventory(),
        expected_version=second.version,
    )
    now = "2026-08-20T00:00:00Z"
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            """
            INSERT INTO artifacts(
                owner_user_id, id, workload_id, revision_id, logical_name,
                digest, mime_type, size_bytes, schema_version, state,
                blob_ref, digest_verified, schema_valid,
                postconditions_valid, created_at, updated_at
            ) VALUES (?, 'artifact-cross-01', ?, ?, 'report', ?,
                      'application/json', 1, 'metnos.test/1', 'committed',
                      'blob:test', 1, 1, 1, ?, ?)
            """,
            (
                "owner-a", first.workload_id, second_revision.revision_id,
                "sha256:" + "a" * 64, now, now,
            ),
        )
    assert first_revision.workload_id == first.workload_id


def test_published_target_requires_a_matching_observed_digest(store):
    draft = store.create_draft(
        "owner-a", "request-publication", redacted_request={"summary": "a"}
    )
    revision = store.admit_revision(
        "owner-a", draft.workload_id, plan(), inventory(),
        expected_version=draft.version,
    )
    now = "2026-08-20T00:00:00Z"
    digest = "sha256:" + "a" * 64
    store._connection.execute(
        """
        INSERT INTO artifacts(
            owner_user_id, id, workload_id, revision_id, logical_name,
            digest, mime_type, size_bytes, schema_version, state,
            blob_ref, digest_verified, schema_valid, postconditions_valid,
            created_at, updated_at
        ) VALUES (?, 'artifact-publish-01', ?, ?, 'report', ?,
                  'application/json', 1, 'metnos.test/1', 'committed',
                  'blob:test', 1, 1, 1, ?, ?)
        """,
        ("owner-a", draft.workload_id, revision.revision_id, digest, now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            """
            INSERT INTO publications(
                owner_user_id, id, artifact_id, target_key, target_redacted,
                state, expected_digest, observed_digest, prepared_at,
                published_at
            ) VALUES (?, 'publication-test-01', 'artifact-publish-01',
                      'internal', 'artifact://report', 'published', ?, ?, ?, ?)
            """,
            ("owner-a", digest, "sha256:" + "b" * 64, now, now),
        )


def test_upgrade_from_previous_schema_fixture():
    from durable_workloads.migrations import _V1_STATEMENTS, utc_now

    connection = open_db(":memory:")
    try:
        applied_at = utc_now()
        for statement in _V1_STATEMENTS:
            connection.execute(statement.replace("__APPLIED_AT__", applied_at))
        assert schema_version(connection) == 1
        assert migrate(connection) == CURRENT_SCHEMA_VERSION
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(outbox)")
        }
        assert {"lease_expires_at", "coalesce_key"}.issubset(columns)
    finally:
        connection.close()
