import sqlite3

import pytest

from executor_birth_epoch_store import (
    BirthLifecycle, EpochCacheKey, EpochState, EpochStoreError,
    get_cache, open_epoch, put_cache, transition_epoch,
    quarantine_for_feedback,
)
from executor_birth_feedback import QuarantineCAS
from manifest_inventory import ContractId, ManifestOrigin


CID = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
G1 = "sha256:" + "1" * 64
G2 = "sha256:" + "2" * 64
NOW = "2030-01-01T00:00:00Z"


def _open(db, generation=G1, lifecycle=BirthLifecycle.PREEXERCISE):
    return open_epoch(contract_id=CID, generation_id=generation, name="demo", source="synt",
                      lifecycle=lifecycle, observed_at=NOW, db_path=db)


def test_epoch_starts_clean_and_schema_has_single_current_index(tmp_path):
    db = tmp_path / "epochs.sqlite"
    record = _open(db)
    assert record.state_version == 1
    connection = sqlite3.connect(db)
    row = connection.execute(
        "SELECT total_calls,successful_calls,failed_calls,positive_feedback,negative_feedback "
        "FROM executor_epochs"
    ).fetchone()
    index_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_epochs_single_current'"
    ).fetchone()[0]
    connection.close()
    assert row == (0, 0, 0, 0, 0)
    assert "WHERE state='current'" in index_sql


def test_two_current_generations_are_rejected_and_transaction_preserves_first(tmp_path):
    db = tmp_path / "epochs.sqlite"
    _open(db, G1)
    with pytest.raises(EpochStoreError, match="epoch_conflict"):
        _open(db, G2)
    connection = sqlite3.connect(db)
    assert connection.execute(
        "SELECT generation_id FROM executor_epochs WHERE state='current'"
    ).fetchall() == [(G1,)]
    connection.close()


def test_sql_index_itself_rejects_two_current_rows_in_one_transaction(tmp_path):
    db = tmp_path / "epochs.sqlite"
    _open(db, G1)
    connection = sqlite3.connect(db)
    connection.execute("DELETE FROM executor_epoch_history")
    connection.execute("DELETE FROM executor_epochs")
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO executor_epochs(contract_id,generation_id,name,source,state,lifecycle,"
        "first_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (CID.value, G1, "demo1", "synt", "current", "preexercise", NOW, NOW, NOW),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO executor_epochs(contract_id,generation_id,name,source,state,lifecycle,"
            "first_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (CID.value, G2, "demo2", "synt", "current", "preexercise", NOW, NOW, NOW),
        )
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM executor_epochs").fetchone()[0] == 0
    connection.close()


def test_cache_requires_exact_typed_triple_and_transition_invalidates(tmp_path):
    db = tmp_path / "epochs.sqlite"
    _open(db)
    key = EpochCacheKey(CID, G1, BirthLifecycle.PREEXERCISE)
    put_cache(key, b"proof", created_at=NOW, db_path=db)
    assert get_cache(key, db_path=db) == b"proof"
    stale = EpochCacheKey(CID, G1, BirthLifecycle.ACTIVE)
    assert get_cache(stale, db_path=db) is None
    with pytest.raises(EpochStoreError, match="cache_key_stale"):
        put_cache(stale, b"legacy-default", created_at=NOW, db_path=db)
    assert transition_epoch(
        key, expected_version=1, new_state=EpochState.CURRENT,
        new_lifecycle=BirthLifecycle.ACTIVE, event_kind="promoted", occurred_at=NOW,
        db_path=db,
    ) == 2
    assert get_cache(key, db_path=db) is None
    assert get_cache(stale, db_path=db) is None


def test_transition_is_cas_and_cache_survives_failed_stale_transition(tmp_path):
    db = tmp_path / "epochs.sqlite"
    _open(db)
    key = EpochCacheKey(CID, G1, BirthLifecycle.PREEXERCISE)
    put_cache(key, b"proof", created_at=NOW, db_path=db)
    with pytest.raises(EpochStoreError, match="epoch_conflict"):
        transition_epoch(
            key, expected_version=9, new_state=EpochState.DEPRECATED,
            new_lifecycle=BirthLifecycle.DEPRECATED, event_kind="retired", occurred_at=NOW,
            db_path=db,
        )
    assert get_cache(key, db_path=db) == b"proof"


@pytest.mark.parametrize(("initial", "target", "event"), [
    (BirthLifecycle.PREEXERCISE, BirthLifecycle.ACTIVE, "promoted"),
    (BirthLifecycle.PREEXERCISE, BirthLifecycle.QUARANTINED, "quarantined"),
    (BirthLifecycle.ACTIVE, BirthLifecycle.DEPRECATED, "retired"),
])
def test_promotion_quarantine_and_retirement_each_invalidate_cache(
        tmp_path, initial, target, event):
    db = tmp_path / f"{event}.sqlite"
    _open(db, lifecycle=initial)
    key = EpochCacheKey(CID, G1, initial)
    put_cache(key, b"proof", created_at=NOW, db_path=db)
    transition_epoch(
        key, expected_version=1,
        new_state=(EpochState.DEPRECATED if target is BirthLifecycle.DEPRECATED
                   else EpochState.CURRENT),
        new_lifecycle=target, event_kind=event, occurred_at=NOW, db_path=db,
    )
    assert get_cache(key, db_path=db) is None


def test_no_legacy_or_untyped_cache_key_default(tmp_path):
    with pytest.raises(EpochStoreError, match="contract_id"):
        EpochCacheKey(CID.value, G1, BirthLifecycle.PREEXERCISE)  # type: ignore[arg-type]
    with pytest.raises(EpochStoreError, match="lifecycle"):
        EpochCacheKey(CID, G1, "preexercise")  # type: ignore[arg-type]


def test_feedback_cas_never_quarantines_successor_b_for_receipt_a(tmp_path):
    db = tmp_path / "epochs.sqlite"
    _open(db, G1, BirthLifecycle.ACTIVE)
    transition_epoch(
        EpochCacheKey(CID, G1, BirthLifecycle.ACTIVE), expected_version=1,
        new_state=EpochState.DEPRECATED, new_lifecycle=BirthLifecycle.DEPRECATED,
        event_kind="superseded", occurred_at=NOW, db_path=db,
    )
    _open(db, G2, BirthLifecycle.ACTIVE)
    assert quarantine_for_feedback(
        contract_id=CID, generation_id=G1, occurred_at=NOW, db_path=db,
    ) is QuarantineCAS.STALE
    connection = sqlite3.connect(db)
    assert connection.execute(
        "SELECT lifecycle,negative_feedback FROM executor_epochs WHERE generation_id=?",
        (G2,),
    ).fetchone() == ("active", 0)
    connection.close()


def test_feedback_quarantine_is_repeatable_after_enqueue_failure(tmp_path):
    db = tmp_path / "epochs.sqlite"
    _open(db, G1, BirthLifecycle.ACTIVE)
    assert quarantine_for_feedback(
        contract_id=CID, generation_id=G1, occurred_at=NOW, db_path=db,
    ) is QuarantineCAS.APPLIED
    assert quarantine_for_feedback(
        contract_id=CID, generation_id=G1, occurred_at=NOW, db_path=db,
    ) is QuarantineCAS.ALREADY_QUARANTINED
    connection = sqlite3.connect(db)
    assert connection.execute(
        "SELECT lifecycle,negative_feedback FROM executor_epochs WHERE generation_id=?",
        (G1,),
    ).fetchone() == ("quarantined", 1)
    connection.close()
