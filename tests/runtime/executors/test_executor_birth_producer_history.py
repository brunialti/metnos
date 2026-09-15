"""Raw history acquisition; these fixtures do not authenticate admissions."""
from __future__ import annotations

import dataclasses
import os
import sqlite3
from pathlib import Path

import pytest

import config
import executor_birth_producer_store as store
from executor_birth_receipts import ReceiptError


def _seed(path: Path):
    db = sqlite3.connect(path)
    path.chmod(0o600)
    db.execute(store._RECEIPT_SCHEMA)
    db.execute(store._ISSUANCE_SCHEMA)
    db.execute("PRAGMA user_version=5")
    for index, state in enumerate(("available", "in_progress", "committed", "rejected"), 1):
        claimed = state != "available"
        terminal = state in {"committed", "rejected"}
        values = (
            f"receipt-{index}", f"hash-{index}", f"opaque-{index}".encode(),
            "issuer", "objective", "source", "builtin", "human",
            "2026-08-25T13:00:00Z", state, "2026-08-25T12:00:00Z",
            f"request-{index}" if claimed else None,
            "2026-08-25T12:00:00Z" if claimed else None,
            "2026-08-25T12:05:00Z" if state == "in_progress" else None,
            "2026-08-25T12:00:30Z" if terminal else None,
            "result" if state == "committed" else None,
            "rejected" if state == "rejected" else None,
            b"opaque terminal" if terminal else None,
            b"opaque auth" if terminal else None,
        )
        # Non-contiguous IDs exercise stable ordering without invented IDs.
        db.execute("INSERT INTO birth_producer_receipts VALUES (" + ",".join("?" * 19) + ")", values)
        db.execute("UPDATE birth_producer_receipts SET rowid=? WHERE receipt_id=?", (index * 10, values[0]))
    # Preserve both kinds of unmatched row; an inner join would lose evidence.
    for index in (1, 2, 3, 9):
        db.execute("INSERT INTO birth_producer_issuance VALUES (?,?,?,?,?,?,?,?)", (
            f"request-{index}", "issuer", "module:operation", "builtin:example/manifest.toml",
            "objective", "source", f"receipt-{index}", f"opaque-{index}".encode(),
        ))
    db.commit()
    return db


@pytest.fixture
def history(tmp_path, monkeypatch):
    # URI metacharacters must remain literal path components.
    root = tmp_path / "state ?# %"
    root.mkdir(mode=0o700)
    directory = root / store.BIRTH_STATE_BASENAME_V1
    directory.mkdir(mode=0o700)
    path = directory / store.PRODUCER_RECEIPTS_BASENAME_V1
    db = _seed(path)
    db.close()
    monkeypatch.setattr(config, "PATH_USER_STATE", root)
    return root, path


def test_complete_history_is_raw_immutable_and_does_not_use_mutating_owners(history, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("history reader invoked initialization or mutation")

    for name in ("_open", "_migrate", "_verify_claimed"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(config, "ensure_dirs", forbidden)
    result = store.read_producer_history_v1()
    assert result.source_path == history[1]
    assert result.schema_version == 5
    assert [row.row_id for row in result.receipts] == [10, 20, 30, 40]
    assert [row.state for row in result.receipts] == ["available", "in_progress", "committed", "rejected"]
    assert len(result.issuances) == 4
    assert result.receipts[3].receipt_id not in {row.receipt_id for row in result.issuances}
    assert result.issuances[3].receipt_id not in {row.receipt_id for row in result.receipts}
    assert result.receipts[2].terminal_envelope == b"opaque terminal"
    assert all(schema.startswith("CREATE TABLE") for _, schema in result.table_definitions)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.receipts[0].state = "committed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.receipts = ()


def test_wal_commit_between_table_reads_does_not_mix_snapshots(history, monkeypatch):
    writer = sqlite3.connect(history[1])
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("UPDATE birth_producer_receipts SET issuer_id='wal-before'")
    writer.commit()
    original = store._read_history_table_v1

    def read_table(db, table, *args, **kwargs):
        result = original(db, table, *args, **kwargs)
        if table == "birth_producer_receipts":
            writer.execute("UPDATE birth_producer_receipts SET issuer_id='wal-after'")
            writer.execute("DELETE FROM birth_producer_issuance WHERE request_id='request-9'")
            writer.commit()
        return result

    monkeypatch.setattr(store, "_read_history_table_v1", read_table)
    try:
        result = store.read_producer_history_v1()
        assert {row.issuer_id for row in result.receipts} == {"wal-before"}
        assert len(result.issuances) == 4
        assert writer.execute("SELECT count(*) FROM birth_producer_issuance").fetchone()[0] == 3
    finally:
        writer.close()


def test_exact_row_and_multibyte_payload_budgets_include_both_tables(history):
    with sqlite3.connect(history[1]) as db:
        db.execute("UPDATE birth_producer_receipts SET issuer_id='é漢字'")
    result = store.read_producer_history_v1()
    expected = sum(len(value.encode("utf-8") if isinstance(value, str) else value)
                   for row in (*result.receipts, *result.issuances)
                   for value in dataclasses.astuple(row)[1:] if value is not None)
    assert result.field_bytes == expected
    assert store.read_producer_history_v1(max_rows=8, max_bytes=expected) == result
    with pytest.raises(ReceiptError, match="history_row_limit"):
        store.read_producer_history_v1(max_rows=7)
    with pytest.raises(ReceiptError, match="history_byte_limit"):
        store.read_producer_history_v1(max_bytes=expected - 1)


@pytest.mark.parametrize("values", [
    {"max_rows": 0}, {"max_rows": True}, {"max_rows": 1.5},
    {"max_bytes": 0}, {"max_bytes": False}, {"max_bytes": -1},
    {"timeout_seconds": 0}, {"timeout_seconds": True},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
])
def test_bad_budgets_refuse_before_access(history, monkeypatch, values):
    monkeypatch.setattr(store, "_history_source_identity_v1", lambda *_: pytest.fail("accessed source"))
    with pytest.raises(ReceiptError, match="history_budget"):
        store.read_producer_history_v1(**values)


@pytest.mark.parametrize("target", ["database", "birth", "state"])
def test_missing_source_is_not_created_or_reported_empty(history, monkeypatch, target):
    root, path = history
    if target == "database":
        path.unlink()
    elif target == "birth":
        path.parent.rename(root / "preserved-birth")
    else:
        monkeypatch.setattr(config, "PATH_USER_STATE", root / "absent")
    with pytest.raises(ReceiptError, match="history_missing"):
        store.read_producer_history_v1()
    selected = Path(config.PATH_USER_STATE) / store.BIRTH_STATE_BASENAME_V1 / store.PRODUCER_RECEIPTS_BASENAME_V1
    assert not selected.exists()
    if target == "state":
        assert path.exists()  # Changing selection must not remove the old DB.
    assert not (root / "absent").exists()


@pytest.mark.parametrize("version", [0, 4, 6])
def test_unsupported_schema_is_not_migrated(history, version):
    with sqlite3.connect(history[1]) as db:
        db.execute(f"PRAGMA user_version={version}")
    before = history[1].read_bytes()
    with pytest.raises(ReceiptError, match="history_schema"):
        store.read_producer_history_v1()
    assert history[1].read_bytes() == before


@pytest.mark.parametrize("alteration", ["column", "view", "blob-as-text", "null-key", "hidden-column"])
def test_unsupported_table_or_storage_type_refuses_whole_inventory(history, alteration):
    with sqlite3.connect(history[1]) as db:
        if alteration == "column":
            db.execute("ALTER TABLE birth_producer_issuance ADD COLUMN unexpected TEXT")
        elif alteration == "hidden-column":
            db.execute("ALTER TABLE birth_producer_issuance ADD COLUMN hidden TEXT GENERATED ALWAYS AS ('x') VIRTUAL")
        elif alteration == "view":
            db.execute("ALTER TABLE birth_producer_issuance RENAME TO saved")
            db.execute("CREATE VIEW birth_producer_issuance AS SELECT * FROM saved")
        elif alteration == "blob-as-text":
            db.execute("UPDATE birth_producer_receipts SET encoded='not-a-blob' WHERE rowid=10")
        else:
            db.execute("UPDATE birth_producer_receipts SET receipt_id=NULL WHERE rowid=10")
    with pytest.raises(ReceiptError, match="history_(schema|value_type)"):
        store.read_producer_history_v1()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file-mode boundary; native ACL proof is separate")
@pytest.mark.parametrize("target", ["database", "birth", "state", "wal", "shm"])
def test_loose_source_metadata_is_not_repaired(history, target):
    root, database = history
    path = {"state": root, "birth": database.parent, "database": database}.get(target)
    if path is None:
        path = database.with_name(database.name + "-" + target)
        path.touch(mode=0o600)
    path.chmod(0o777 if path.is_dir() else 0o644)
    before = path.stat().st_mode
    with pytest.raises(ReceiptError, match="history_permissions"):
        store.read_producer_history_v1()
    assert path.stat().st_mode == before


@pytest.mark.parametrize("target", ["database", "birth", "wal", "shm"])
def test_linked_source_is_refused(history, target):
    _, database = history
    path = database.parent if target == "birth" else database
    if target in {"wal", "shm"}:
        path = database.with_name(database.name + "-" + target)
        path.symlink_to(database)
    else:
        preserved = path.with_name(path.name + "-preserved")
        path.rename(preserved)
        path.symlink_to(preserved, target_is_directory=target == "birth")
    with pytest.raises(ReceiptError, match="history_file_kind"):
        store.read_producer_history_v1()


def test_database_replacement_during_scan_is_refused(history, monkeypatch):
    original = store._read_history_table_v1

    def replace_after_read(db, table, *args, **kwargs):
        result = original(db, table, *args, **kwargs)
        if table == "birth_producer_issuance":
            path = history[1]
            path.rename(path.with_name("preserved.sqlite"))
            replacement = _seed(path)
            replacement.close()
        return result

    monkeypatch.setattr(store, "_read_history_table_v1", replace_after_read)
    with pytest.raises(ReceiptError, match="history_source_changed"):
        store.read_producer_history_v1()


def test_deadline_in_python_loop_refuses_instead_of_returning_partial(history, monkeypatch):
    original = store._read_history_table_v1

    def expire_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(store.time, "monotonic", lambda: kwargs["deadline"] + 1)
        return result

    monkeypatch.setattr(store, "_read_history_table_v1", expire_after_read)
    with pytest.raises(ReceiptError, match="history_timeout"):
        store.read_producer_history_v1()


def test_database_connection_is_logically_read_only(history, monkeypatch):
    original = store._read_history_table_v1

    def check_read_only(db, *args, **kwargs):
        assert db.in_transaction
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM birth_producer_receipts")
        db.execute("PRAGMA query_only=OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM birth_producer_receipts")
        db.execute("PRAGMA query_only=ON")
        return original(db, *args, **kwargs)

    monkeypatch.setattr(store, "_read_history_table_v1", check_read_only)
    before = history[1].read_bytes()
    store.read_producer_history_v1()
    assert history[1].read_bytes() == before


def test_existing_empty_schema_is_distinct_from_missing_database(history):
    with sqlite3.connect(history[1]) as db:
        db.execute("DELETE FROM birth_producer_issuance")
        db.execute("DELETE FROM birth_producer_receipts")
    result = store.read_producer_history_v1()
    assert result.receipts == result.issuances == ()
    assert result.field_bytes == 0


def test_per_row_bound_counts_aggregate_bytes_not_individual_fields(history):
    original = store.read_producer_history_v1().receipts[0]
    other_bytes = sum(len(value.encode() if isinstance(value, str) else value)
                      for value in dataclasses.astuple(original)[1:] if value is not None)
    padding = store._HISTORY_ROW_BYTES - other_bytes + len(original.encoded)
    with sqlite3.connect(history[1]) as db:
        db.execute("UPDATE birth_producer_receipts SET encoded=? WHERE rowid=10", (b"x" * padding,))
    assert len(store.read_producer_history_v1().receipts[0].encoded) == padding
    with sqlite3.connect(history[1]) as db:
        db.execute("UPDATE birth_producer_receipts SET encoded=? WHERE rowid=10", (b"x" * (padding + 1),))
    with pytest.raises(ReceiptError, match="history_byte_limit"):
        store.read_producer_history_v1()


def test_oversized_schema_is_refused_before_selecting_its_text(history, monkeypatch):
    with sqlite3.connect(history[1]) as db:
        db.execute("PRAGMA writable_schema=ON")
        db.execute("UPDATE sqlite_schema SET sql=sql || ? WHERE name='birth_producer_receipts'",
                   (" /*" + "x" * 65536 + "*/",))
    connect = sqlite3.connect
    statements = []

    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store.sqlite3, "connect", traced)
    with pytest.raises(ReceiptError, match="history_schema"):
        store.read_producer_history_v1()
    assert not any(statement.startswith("SELECT sql ") for statement in statements)


def test_hardlinked_database_is_refused(history):
    os.link(history[1], history[0] / "second-name.sqlite")
    with pytest.raises(ReceiptError, match="history_file_kind"):
        store.read_producer_history_v1()


def test_symlinked_selected_state_is_refused(history, monkeypatch):
    selected = history[0].with_name("state-alias")
    selected.symlink_to(history[0], target_is_directory=True)
    monkeypatch.setattr(config, "PATH_USER_STATE", selected)
    with pytest.raises(ReceiptError, match="history_path"):
        store.read_producer_history_v1()


def test_corrupt_database_is_not_repaired(history):
    history[1].write_bytes(b"not a sqlite database")
    with pytest.raises(ReceiptError, match="history_unreadable"):
        store.read_producer_history_v1()
    assert history[1].read_bytes() == b"not a sqlite database"


def test_sql_busy_wait_respects_deadline(history):
    writer = sqlite3.connect(history[1])
    writer.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(ReceiptError, match="history_(timeout|unreadable)"):
            store.read_producer_history_v1(timeout_seconds=0.03)
    finally:
        writer.rollback()
        writer.close()


def test_large_finite_timeout_does_not_overflow_busy_timeout(history):
    assert len(store.read_producer_history_v1(timeout_seconds=1e308).receipts) == 4


def test_unrepresentable_timeout_is_a_declared_budget_error(history):
    with pytest.raises(ReceiptError, match="history_budget"):
        store.read_producer_history_v1(timeout_seconds=10 ** 1000)


def test_blob_schema_sql_is_not_coerced_or_allowed_to_escape_as_type_error(history):
    with sqlite3.connect(history[1]) as db:
        db.execute("PRAGMA writable_schema=ON")
        db.execute("UPDATE sqlite_schema SET sql=CAST(sql AS BLOB) WHERE name='birth_producer_receipts'")
    with pytest.raises(ReceiptError, match="history_schema"):
        store.read_producer_history_v1()
