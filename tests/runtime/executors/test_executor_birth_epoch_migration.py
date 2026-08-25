import sqlite3

import pytest

import executor_birth_epoch_store as store
from executor_birth_epoch_store import (
    EPOCH_STORE_SCHEMA_VERSION,
    EpochStoreError,
    legacy_rows_digest,
    migrate_legacy_rows,
)


NOW = "2030-01-01T00:00:00Z"
ROWS = (
    {"name": "second", "calls": 2, "flags": ["b", "a"]},
    {"name": "first", "calls": 1, "enabled": True},
)


def _migrate(db, rows=ROWS, **overrides):
    arguments = {
        "legacy_table": "executor_usage",
        "rows": rows,
        "expected_count": len(rows),
        "expected_digest": legacy_rows_digest(rows),
        "migrated_at": NOW,
        "db_path": db,
    }
    arguments.update(overrides)
    return migrate_legacy_rows(**arguments)


def test_migration_is_versioned_lossless_unresolved_and_preserves_source(tmp_path):
    db = tmp_path / "epochs.sqlite"
    source = sqlite3.connect(db)
    source.execute("CREATE TABLE executor_usage(name TEXT,calls INTEGER)")
    source.executemany("INSERT INTO executor_usage VALUES(?,?)", (("second", 2), ("first", 1)))
    source.commit()
    source.close()

    assert _migrate(db) == 2
    connection = sqlite3.connect(db)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == EPOCH_STORE_SCHEMA_VERSION
    assert connection.execute("SELECT * FROM executor_usage ORDER BY name").fetchall() == [
        ("first", 1), ("second", 2),
    ]
    copied = connection.execute(
        "SELECT legacy_name,resolution FROM executor_legacy_state ORDER BY legacy_name"
    ).fetchall()
    assert copied == [("first", "unresolved"), ("second", "unresolved")]
    assert connection.execute("SELECT COUNT(*) FROM executor_epochs").fetchone()[0] == 0
    connection.close()


def test_digest_is_canonical_order_independent_and_preserves_duplicates(tmp_path):
    duplicate = ({"name": "same", "calls": 1}, {"calls": 1, "name": "same"})
    assert legacy_rows_digest(ROWS) == legacy_rows_digest(tuple(reversed(ROWS)))
    db = tmp_path / "duplicates.sqlite"
    assert _migrate(db, duplicate) == 2
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT COUNT(*) FROM executor_legacy_state").fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM executor_legacy_migration_rows"
    ).fetchone()[0] == 2
    connection.close()


@pytest.mark.parametrize("field", ["count", "digest"])
def test_source_count_or_digest_mismatch_fails_before_any_write(tmp_path, field):
    db = tmp_path / f"bad-{field}.sqlite"
    kwargs = ({"expected_count": 9} if field == "count" else
              {"expected_digest": "sha256:" + "f" * 64})
    with pytest.raises(EpochStoreError) as raised:
        _migrate(db, **kwargs)
    assert raised.value.code == f"legacy_migration_{field}_mismatch"
    assert not db.exists()


def test_exact_retry_is_idempotent_but_changed_source_fails_closed(tmp_path):
    db = tmp_path / "retry.sqlite"
    assert _migrate(db) == 2
    assert _migrate(db, tuple(reversed(ROWS))) == 0
    changed = ROWS + ({"name": "later", "calls": 3},)
    with pytest.raises(EpochStoreError) as raised:
        _migrate(db, changed)
    assert raised.value.code == "legacy_migration_source_mismatch"
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT COUNT(*) FROM executor_legacy_state").fetchone()[0] == 2
    connection.close()


def test_retry_detects_target_count_and_digest_corruption(tmp_path):
    for corruption, expected_code in (
        ("DELETE FROM executor_legacy_migration_rows WHERE source_ordinal=0",
         "legacy_migration_count_mismatch"),
        ("UPDATE executor_legacy_state SET legacy_row_json='{}' WHERE legacy_id=1",
         "legacy_migration_digest_mismatch"),
    ):
        db = tmp_path / (expected_code + ".sqlite")
        _migrate(db)
        connection = sqlite3.connect(db)
        connection.execute(corruption)
        connection.commit()
        connection.close()
        with pytest.raises(EpochStoreError) as raised:
            _migrate(db)
        assert raised.value.code == expected_code


def test_mid_copy_crash_rolls_back_and_retry_completes(tmp_path, monkeypatch):
    db = tmp_path / "crash.sqlite"
    original = store._insert_legacy_row
    calls = 0

    def crash_after_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        original(*args, **kwargs)
        if calls == 1:
            raise RuntimeError("injected crash")

    monkeypatch.setattr(store, "_insert_legacy_row", crash_after_first)
    with pytest.raises(RuntimeError, match="injected crash"):
        _migrate(db)
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT COUNT(*) FROM executor_legacy_state").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM executor_legacy_migrations").fetchone()[0] == 0
    connection.close()
    monkeypatch.setattr(store, "_insert_legacy_row", original)
    assert _migrate(db) == 2


def test_unknown_version_and_tampered_schema_fail_closed(tmp_path):
    unknown = tmp_path / "unknown.sqlite"
    connection = sqlite3.connect(unknown)
    connection.execute("PRAGMA user_version=99")
    connection.close()
    with pytest.raises(EpochStoreError) as raised:
        _migrate(unknown)
    assert raised.value.code == "epoch_schema_version"

    tampered = tmp_path / "tampered.sqlite"
    _migrate(tampered)
    connection = sqlite3.connect(tampered)
    connection.execute("DROP INDEX idx_epochs_single_current")
    connection.close()
    with pytest.raises(EpochStoreError) as raised:
        _migrate(tampered)
    assert raised.value.code == "epoch_schema_mismatch"


@pytest.mark.parametrize("bad", [1.5, float("nan"), {1: "ambiguous"}])
def test_noncanonical_legacy_values_are_rejected(tmp_path, bad):
    rows = ({"name": "bad", "value": bad},)
    with pytest.raises(EpochStoreError, match="legacy_migration_invalid"):
        legacy_rows_digest(rows)
