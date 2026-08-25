import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_retention as retention
from executor_birth_retention import (
    NodeKey, NodeState, NodeType, RetentionError, mark, put_node, sweep,
)
from executor_birth_retention_integration import (
    PRODUCTIVE_OWNER_ADAPTERS, OwnerAdapterRegistry, RetentionIntegrationError, drain_outbox,
    enqueue_owner_event, pending_events,
)


OLD = "2026-01-01T00:00:00Z"
NOW = "2026-08-25T12:00:00Z"
LATER = "2026-08-25T12:00:01Z"


class SameDatabaseEvidenceAdapter:
    node_type = NodeType.EVIDENCE

    def reconcile(self, connection, event):
        assert set(event.payload) == {"eligible_after", "owner_value"}
        connection.execute(
            "INSERT INTO owner_same_database(node_id,value) VALUES(?,?) "
            "ON CONFLICT(node_id) DO UPDATE SET value=excluded.value",
            (event.key.node_id, event.payload["owner_value"]),
        )
        row = connection.execute(
            "SELECT object_version,state FROM retention_nodes "
            "WHERE node_type=? AND node_id=?",
            (event.key.node_type.value, event.key.node_id),
        ).fetchone()
        version = 1 if row is None else int(row[0]) + 1
        if row is not None and row[1] == "deleted":
            raise RetentionIntegrationError(
                "retention_state_changed", "deleted target")
        connection.execute(
            "INSERT INTO retention_nodes VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(node_type,node_id) DO UPDATE SET "
            "object_version=excluded.object_version,state=excluded.state,"
            "closed_at=excluded.closed_at,eligible_after=excluded.eligible_after",
            (event.key.node_type.value, event.key.node_id, version, "closed",
             event.created_at, event.created_at,
             event.payload["eligible_after"]),
        )
        connection.execute(
            "UPDATE retention_meta SET graph_version=graph_version+1 WHERE singleton=1"
        )


def prepare_owner_table(db):
    # A test-only co-located owner.  No productive adapter is claimed.
    connection = retention._open(db)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS owner_same_database("
        "node_id TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    connection.close()


def payload(value="owned"):
    return {"eligible_after": OLD, "owner_value": value}


def test_registry_is_exact_closed_and_has_no_default():
    assert PRODUCTIVE_OWNER_ADAPTERS.node_types == ()
    registry = OwnerAdapterRegistry((SameDatabaseEvidenceAdapter(),))
    assert registry.node_types == (NodeType.EVIDENCE,)
    assert isinstance(registry.resolve(NodeType.EVIDENCE),
                      SameDatabaseEvidenceAdapter)
    with pytest.raises(RetentionIntegrationError,
                       match="retention_owner_adapter_missing"):
        registry.resolve(NodeType.GENERATION)
    with pytest.raises(RetentionIntegrationError,
                       match="retention_owner_adapter_duplicate"):
        OwnerAdapterRegistry((SameDatabaseEvidenceAdapter(),
                              SameDatabaseEvidenceAdapter()))


def test_pending_event_is_a_global_conservative_root(tmp_path):
    db = tmp_path / "retention.sqlite"
    orphan = NodeKey(NodeType.BLOB, "otherwise-collectable")
    put_node(orphan, state=NodeState.CLOSED, created_at=OLD,
             eligible_after=OLD, db_path=db)
    enqueue_owner_event(
        key=NodeKey(NodeType.EVIDENCE, "not-yet-reconciled"),
        payload=payload(), created_at=NOW, db_path=db,
    )
    assert mark(run_id="blocked-by-outbox", observed_at=NOW, db_path=db) == ()


def test_event_arriving_between_mark_and_sweep_wins_the_race(tmp_path):
    db = tmp_path / "retention.sqlite"
    candidate = NodeKey(NodeType.BLOB, "marked-before-event")
    put_node(candidate, state=NodeState.CLOSED, created_at=OLD,
             eligible_after=OLD, db_path=db)
    assert mark(run_id="race", observed_at=NOW, db_path=db) == (candidate,)
    enqueue_owner_event(
        key=NodeKey(NodeType.EVIDENCE, "late-owner-event"),
        payload=payload(), created_at=NOW, db_path=db,
    )
    private = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
    result = sweep(
        run_id="race", observed_at=NOW, receipt_key_id="race-v1",
        receipt_private_key=private,
        receipt_public_keys={"race-v1": private.public_key()}, db_path=db,
        delete_object=lambda *_: pytest.fail("pending outbox reached delete"),
    )
    assert result.deleted == ()
    assert result.error_codes == ((candidate, "retention_referenced"),)


def test_absent_adapter_remains_durable_and_blocks_collection(tmp_path):
    db = tmp_path / "retention.sqlite"
    orphan = NodeKey(NodeType.BLOB, "preserved")
    put_node(orphan, state=NodeState.CLOSED, created_at=OLD,
             eligible_after=OLD, db_path=db)
    event_id = enqueue_owner_event(
        key=NodeKey(NodeType.GENERATION, "no-owner"), payload=payload(),
        created_at=NOW, db_path=db,
    )
    with pytest.raises(RetentionIntegrationError,
                       match="retention_owner_adapter_missing"):
        drain_outbox(registry=OwnerAdapterRegistry(()), applied_at=NOW,
                     db_path=db)
    event = pending_events(db_path=db)[0]
    assert event.event_id == event_id
    assert event.state.value == "pending"
    assert event.attempts == 1
    assert event.last_error == "retention_owner_adapter_missing"
    assert mark(run_id="still-blocked", observed_at=NOW, db_path=db) == ()


def test_crash_after_adapter_rolls_back_owner_graph_and_journal(tmp_path):
    db = tmp_path / "retention.sqlite"
    prepare_owner_table(db)
    key = NodeKey(NodeType.EVIDENCE, "atomic-owner")
    event_id = enqueue_owner_event(
        key=key, payload=payload(), created_at=NOW, db_path=db)
    registry = OwnerAdapterRegistry((SameDatabaseEvidenceAdapter(),))

    def crash(_event):
        raise OSError("injected crash boundary")

    with pytest.raises(RetentionIntegrationError,
                       match="retention_owner_adapter_failed"):
        drain_outbox(registry=registry, applied_at=NOW, db_path=db,
                     after_adapter=crash)
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT count(*) FROM owner_same_database").fetchone()[0] == 0
    assert connection.execute(
        "SELECT count(*) FROM retention_nodes WHERE node_id='atomic-owner'"
    ).fetchone()[0] == 0
    state = connection.execute(
        "SELECT state,attempts,last_error FROM retention_outbox WHERE event_id=?",
        (event_id,),
    ).fetchone()
    connection.close()
    assert state == ("pending", 1, "retention_owner_adapter_failed")

    assert drain_outbox(registry=registry, applied_at=NOW,
                        db_path=db) == (event_id,)
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT count(*) FROM owner_same_database").fetchone()[0] == 1
    assert connection.execute(
        "SELECT state,attempts,last_error FROM retention_outbox WHERE event_id=?",
        (event_id,),
    ).fetchone() == ("applied", 2, None)
    connection.close()


def test_enqueue_and_drain_are_idempotent(tmp_path):
    db = tmp_path / "retention.sqlite"
    prepare_owner_table(db)
    key = NodeKey(NodeType.EVIDENCE, "idempotent")
    first = enqueue_owner_event(
        key=key, payload=payload(), created_at=NOW, db_path=db)
    second = enqueue_owner_event(
        key=key, payload=payload(), created_at=NOW, db_path=db)
    assert first == second
    registry = OwnerAdapterRegistry((SameDatabaseEvidenceAdapter(),))
    assert drain_outbox(registry=registry, applied_at=NOW,
                        db_path=db) == (first,)
    assert drain_outbox(registry=registry, applied_at=NOW,
                        db_path=db) == ()

    # The same owner state observed in a later source event is distinct; an
    # exact retry remains the tuple including its original timestamp.
    later = enqueue_owner_event(
        key=key, payload=payload(), created_at=LATER, db_path=db)
    assert later != first


def test_outbox_tamper_fails_closed_and_remains_a_root(tmp_path):
    db = tmp_path / "retention.sqlite"
    event_id = enqueue_owner_event(
        key=NodeKey(NodeType.EVIDENCE, "tampered"), payload=payload(),
        created_at=NOW, db_path=db,
    )
    connection = sqlite3.connect(db)
    connection.execute(
        "UPDATE retention_outbox SET payload_json='{}' WHERE event_id=?",
        (event_id,),
    )
    connection.commit()
    connection.close()
    with pytest.raises(RetentionIntegrationError,
                       match="retention_outbox_invalid"):
        pending_events(db_path=db)


def test_exact_v1_database_migrates_once_to_v2(tmp_path):
    db = tmp_path / "retention.sqlite"
    connection = sqlite3.connect(db, isolation_level=None)
    connection.executescript(retention._SCHEMA_V1)
    connection.execute("PRAGMA user_version=1")
    connection.execute(
        "INSERT INTO retention_nodes VALUES(?,?,?,?,?,?,?)",
        ("evidence", "preserved-v1", 1, "closed", OLD, OLD, OLD),
    )
    connection.close()

    assert pending_events(db_path=db) == ()
    connection = sqlite3.connect(db)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert connection.execute(
        "SELECT node_id FROM retention_nodes"
    ).fetchone()[0] == "preserved-v1"
    connection.close()


def test_tampered_v1_database_is_not_migrated(tmp_path):
    db = tmp_path / "retention.sqlite"
    connection = sqlite3.connect(db, isolation_level=None)
    connection.executescript(retention._SCHEMA_V1)
    connection.execute("PRAGMA user_version=1")
    connection.execute("DROP TRIGGER retention_no_edge_while_deleting")
    connection.close()
    with pytest.raises(RetentionError,
                       match="retention_schema_mismatch: v1 migration source"):
        pending_events(db_path=db)
