"""Portable certification of the inactive RM-0008 F6 storage foundation.

This certifies only the SQLite format and minimal receipt codec.  It does not
claim that the collector is connected to any productive owner.
"""
from __future__ import annotations

import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_retention import (
    NodeKey, NodeState, NodeType, RetentionError, mark, put_node, sweep,
    verify_minimal_receipt,
)


OLD = "2026-01-01T00:00:00Z"
NOW = "2026-08-25T12:00:00Z"


def test_v1_database_and_signed_receipt_round_trip(tmp_path):
    database = tmp_path / "retention.sqlite"
    key = NodeKey(NodeType.EVIDENCE, "portable-evidence")
    private = Ed25519PrivateKey.from_private_bytes(b"p" * 32)
    put_node(key, state=NodeState.CLOSED, created_at=OLD,
             eligible_after=OLD, db_path=database)
    assert mark(run_id="portable-run", observed_at=NOW,
                db_path=database) == (key,)
    result = sweep(
        run_id="portable-run", observed_at=NOW,
        receipt_key_id="portable-v1", receipt_private_key=private,
        receipt_public_keys={"portable-v1": private.public_key()},
        db_path=database, delete_object=lambda _key, _guard: None,
    )
    assert result.deleted == (key,)
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    row = connection.execute(
        "SELECT run_id,object_version,deleted_at,authentication "
        "FROM retention_receipts"
    ).fetchone()
    connection.close()
    assert verify_minimal_receipt(
        key=key, run_id=row[0], object_version=row[1], deleted_at=row[2],
        authentication=row[3], public_keys={"portable-v1": private.public_key()},
    ) == "portable-v1"


def test_unversioned_retention_lookalike_is_never_adopted(tmp_path):
    database = tmp_path / "retention.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE retention_nodes(node_type TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(RetentionError, match="retention_schema_version: unversioned"):
        put_node(
            NodeKey(NodeType.EVIDENCE, "untrusted"), state=NodeState.CLOSED,
            created_at=OLD, eligible_after=OLD, db_path=database,
        )


def test_v1_schema_tamper_is_rejected_before_graph_use(tmp_path):
    database = tmp_path / "retention.sqlite"
    put_node(
        NodeKey(NodeType.EVIDENCE, "tampered"), state=NodeState.CLOSED,
        created_at=OLD, eligible_after=OLD, db_path=database,
    )
    connection = sqlite3.connect(database)
    connection.execute("DROP TRIGGER retention_no_root_while_deleting")
    connection.commit()
    connection.close()
    with pytest.raises(RetentionError, match="retention_schema_mismatch: objects"):
        mark(run_id="tampered", observed_at=NOW, db_path=database)
