"""Portable, lossless F5 migration storage; no installed cutover is claimed."""
import sqlite3
import struct

import pytest

import executor_birth_epoch_store as store
from executor_birth_epoch_store import (
    EPOCH_STORE_SCHEMA_VERSION,
    EpochStoreError,
    legacy_rows_digest,
    migrate_legacy_rows,
)


NOW = "2030-01-01T00:00:00Z"
SOURCE = "sha256:" + "1" * 64
SCHEMA = "sha256:" + "2" * 64
ROWS = (
    {"name": "second", "calls": 2, "flags": ["b", "a"]},
    {"name": "first", "calls": 1, "enabled": True},
)


def _migrate(db, rows=ROWS, **overrides):
    arguments = {
        "source_id": SOURCE,
        "source_schema_id": SCHEMA,
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


@pytest.mark.parametrize("bad", [object(), {1: "ambiguous"}, {"not", "ordered"}])
def test_noncanonical_legacy_values_are_rejected(tmp_path, bad):
    rows = ({"name": "bad", "value": bad},)
    with pytest.raises(EpochStoreError, match="legacy_migration_invalid"):
        legacy_rows_digest(rows)


def test_same_table_from_different_sources_is_preserved_separately(tmp_path):
    db = tmp_path / "epochs.sqlite"
    assert _migrate(db) == 2
    assert _migrate(db, source_id="sha256:" + "3" * 64) == 2
    assert _migrate(db) == 0
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM executor_legacy_state").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM executor_legacy_migrations").fetchone()[0] == 2


def test_schema_change_is_not_an_equivalent_retry(tmp_path):
    db = tmp_path / "epochs.sqlite"
    _migrate(db)
    with pytest.raises(EpochStoreError, match="legacy_migration_source_mismatch"):
        _migrate(db, source_schema_id="sha256:" + "4" * 64)


@pytest.mark.parametrize("name", [None, 42, b"binary-name", "", " malformed ", "nul\0name", "bad\ud800text"])
def test_malformed_name_preserves_the_complete_row_without_inventing_identity(tmp_path, name):
    db = tmp_path / "epochs.sqlite"
    row = {"name": name, "last_used": 1.5, "blob": b"\0\xff"}
    assert _migrate(db, (row,)) == 1
    with sqlite3.connect(db) as connection:
        projected, encoded = connection.execute(
            "SELECT legacy_name,legacy_row_json FROM executor_legacy_state"
        ).fetchone()
        assert projected is None
        assert store.decode_legacy_row(encoded) == row
        assert connection.execute("SELECT COUNT(*) FROM executor_epochs").fetchone()[0] == 0


@pytest.mark.parametrize("value", [None, True, False, 0, -2**63, 2**63-1, 1.5, -0.0,
                                   float("inf"), float("-inf"), float("nan"), "è\0", b"\0\xff"])
def test_typed_preservation_round_trips_without_coercion(value):
    row = {"name": "legacy", "value": value}
    encoded = store._encode_legacy_rows((row,))[0][1]
    restored = store.decode_legacy_row(encoded)["value"]
    assert type(restored) is type(value)
    if type(value) is float:
        assert struct.pack(">d", restored) == struct.pack(">d", value)
    else:
        assert restored == value


def test_typed_values_do_not_collide_with_json_lookalikes():
    values = (1, True, "1", 1.0, b"1", ["int", "1"], {"blob": "MQ=="}, None)
    assert len({legacy_rows_digest(({"name": "same", "value": value},))
                for value in values}) == len(values)


def test_retry_after_separate_resolution_preserves_original_evidence(tmp_path):
    db = tmp_path / "epochs.sqlite"
    _migrate(db)
    with sqlite3.connect(db) as connection:
        original = connection.execute("SELECT * FROM executor_legacy_state").fetchall()
        connection.execute(
            "INSERT INTO executor_legacy_resolutions VALUES(?,?,?,?,?,?)",
            (original[0][0], "discarded", None, None, "sha256:" + "9" * 64, NOW),
        )
    assert _migrate(db) == 0
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT * FROM executor_legacy_state").fetchall() == original
        assert connection.execute("SELECT COUNT(*) FROM executor_legacy_resolutions").fetchone()[0] == 1


def test_real_sqlite_acquisition_copy_and_independent_reread(tmp_path):
    source, target = tmp_path / "legacy.sqlite", tmp_path / "epochs.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE usage(name,value,extra)")
        connection.executemany("INSERT INTO usage VALUES(?,?,?)", (
            (None, 3.25, b"\0\xff"), ("demo", -2**63, None),
            ("demo", -2**63, None), (b"broken", "text\0", float("inf")),
        ))
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = tuple(dict(row) for row in connection.execute("SELECT * FROM usage"))
    assert _migrate(target, rows) == 4
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as connection:
        copied = tuple(store.decode_legacy_row(row[0]) for row in connection.execute(
            "SELECT legacy_row_json FROM executor_legacy_state ORDER BY legacy_id"))
    assert legacy_rows_digest(copied) == legacy_rows_digest(rows)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        assert tuple(dict(row) for row in connection.execute("SELECT * FROM usage")) == rows


@pytest.mark.parametrize("version", [1, 2, 99])
def test_older_and_unknown_epoch_versions_are_not_silently_reinterpreted(tmp_path, version):
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    with pytest.raises(EpochStoreError, match="epoch_schema_version"):
        _migrate(db)


@pytest.mark.parametrize("statement", [
    "UPDATE executor_legacy_state SET legacy_name='different' WHERE legacy_id=1",
    "UPDATE executor_legacy_state SET legacy_table='other' WHERE legacy_id=1",
    "UPDATE executor_legacy_migration_rows SET source_ordinal=8 WHERE source_ordinal=1",
])
def test_preservation_binding_tamper_is_not_an_equivalent_retry(tmp_path, statement):
    db = tmp_path / "epochs.sqlite"
    _migrate(db)
    with sqlite3.connect(db) as connection:
        connection.execute(statement)
    with pytest.raises(EpochStoreError, match="legacy_migration_schema_mismatch"):
        _migrate(db)


@pytest.mark.parametrize("encoded", [
    '["object",[["name",["text","a"]],["name",["text","b"]]]]',
    '["object",[["n",["int","01"]]]]',
    '["object",[["n",["real","00"]]]]',
    '["object",[["n",["blob","!"]]]]',
    '["object",[["n",["bool",1]]]]',
    '["object",[["n",["unknown",1]]]]',
    '["object",[]] ', 'null', '{}',
])
def test_preservation_decoder_refuses_ambiguous_or_noncanonical_values(encoded):
    with pytest.raises(EpochStoreError, match="legacy_migration_invalid"):
        store.decode_legacy_row(encoded)
