from pathlib import Path

import pytest

from executor_birth_retention import (
    CandidateStatus, EdgeState, NodeKey, NodeState, NodeType, RetentionError,
    add_edge, add_root, close_edge, historical_generation_selectable, mark,
    put_node, remove_root, sweep,
)


OLD = "2026-01-01T00:00:00Z"
NOW = "2026-08-25T12:00:00Z"
FUTURE = "2027-01-01T00:00:00Z"
KEY = b"k" * 32


def node(kind, identity):
    return NodeKey(kind, identity)


def closed(db: Path, key: NodeKey, *, eligible=OLD):
    put_node(key, state=NodeState.CLOSED, created_at=OLD,
             eligible_after=eligible, db_path=db)


def test_reachability_preserves_complete_birth_chain_and_collects_orphan(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "sha256:" + "a" * 64)
    admission = node(NodeType.ADMISSION_RECEIPT, "sha256:" + "b" * 64)
    producer = node(NodeType.PRODUCER_RECEIPT, "sha256:" + "c" * 64)
    review = node(NodeType.REVIEW, "sha256:" + "d" * 64)
    evidence = node(NodeType.EVIDENCE, "sha256:" + "e" * 64)
    approval = node(NodeType.APPROVAL, "sha256:" + "f" * 64)
    report = node(NodeType.BIRTH_REPORT, "sha256:" + "1" * 64)
    orphan = node(NodeType.EVIDENCE, "sha256:" + "2" * 64)
    for key in (generation, admission, producer, review, evidence, approval, report, orphan):
        closed(db, key)
    add_root(generation, root_kind="current_pointer", db_path=db)
    for source, target, relation in (
        (generation, admission, "admission"), (admission, producer, "provenance"),
        (admission, review, "review"), (review, evidence, "evidence"),
        (admission, approval, "approval"), (admission, report, "birth_report"),
    ):
        add_edge(source, target, edge_type=relation, state=EdgeState.CLOSED,
                 created_at=OLD, db_path=db)
    assert mark(run_id="run-1", observed_at=NOW, db_path=db) == (orphan,)
    result = sweep(run_id="run-1", observed_at=NOW, receipt_key=KEY, db_path=db)
    assert result.deleted == (orphan,)


def test_open_reference_is_conservative_root(tmp_path):
    db = tmp_path / "retention.sqlite"
    report = node(NodeType.BIRTH_REPORT, "report")
    evidence = node(NodeType.EVIDENCE, "evidence")
    closed(db, report)
    closed(db, evidence)
    add_edge(report, evidence, edge_type="evidence", state=EdgeState.OPEN,
             created_at=OLD, db_path=db)
    assert mark(run_id="run-open", observed_at=NOW, db_path=db) == ()
    close_edge(report, evidence, edge_type="evidence", closed_at=NOW, db_path=db)
    assert set(mark(run_id="run-closed", observed_at=NOW, db_path=db)) == {report, evidence}


def test_removed_pointer_stops_being_a_root(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "old-current")
    closed(db, generation)
    add_root(generation, root_kind="current_pointer", db_path=db)
    assert mark(run_id="rooted", observed_at=NOW, db_path=db) == ()
    remove_root(generation, root_kind="current_pointer", db_path=db)
    assert mark(run_id="unrooted", observed_at=NOW, db_path=db) == (generation,)


def test_mark_sweep_race_new_root_wins(tmp_path):
    db = tmp_path / "retention.sqlite"
    candidate = node(NodeType.GENERATION, "historic")
    closed(db, candidate)
    assert mark(run_id="race", observed_at=NOW, db_path=db) == (candidate,)

    called = False
    def race(key):
        nonlocal called
        if not called:
            called = True
            add_root(key, root_kind="rollback_request", db_path=db)

    result = sweep(run_id="race", observed_at=NOW, receipt_key=KEY,
                   db_path=db, before_each=race)
    assert result.deleted == ()
    assert result.preserved == ((candidate, CandidateStatus.REFERENCED),)


def test_version_change_and_ttl_change_are_not_deleted(tmp_path):
    db = tmp_path / "retention.sqlite"
    changed = node(NodeType.EVIDENCE, "changed")
    window = node(NodeType.APPROVAL, "window")
    closed(db, changed)
    closed(db, window)
    mark(run_id="versions", observed_at=NOW, db_path=db)
    put_node(changed, state=NodeState.CLOSED, created_at=NOW,
             eligible_after=OLD, db_path=db)
    put_node(window, state=NodeState.CLOSED, created_at=NOW,
             eligible_after=FUTURE, db_path=db)
    result = sweep(run_id="versions", observed_at=NOW, receipt_key=KEY, db_path=db)
    assert dict(result.preserved) == {
        changed: CandidateStatus.STATE_CHANGED,
        window: CandidateStatus.STATE_CHANGED,
    }


def test_double_sweep_is_idempotent_and_receipt_is_unique(tmp_path):
    db = tmp_path / "retention.sqlite"
    key = node(NodeType.EVIDENCE, "once")
    closed(db, key)
    mark(run_id="double", observed_at=NOW, db_path=db)
    first = sweep(run_id="double", observed_at=NOW, receipt_key=KEY, db_path=db)
    second = sweep(run_id="double", observed_at=NOW, receipt_key=KEY, db_path=db)
    assert first.deleted == (key,)
    assert second.deleted == ()
    import sqlite3
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT count(*) FROM retention_receipts").fetchone()[0] == 1


def test_sweep_deletes_referenced_leaf_before_owner(tmp_path):
    db = tmp_path / "retention.sqlite"
    owner = node(NodeType.BIRTH_REPORT, "owner")
    leaf = node(NodeType.EVIDENCE, "leaf")
    closed(db, owner)
    closed(db, leaf)
    add_edge(owner, leaf, edge_type="evidence", state=EdgeState.CLOSED,
             created_at=OLD, db_path=db)
    mark(run_id="leaves", observed_at=NOW, db_path=db)
    result = sweep(run_id="leaves", observed_at=NOW, receipt_key=KEY, db_path=db)
    assert result.deleted == (leaf, owner)


def test_missing_edge_endpoint_is_rejected_atomically(tmp_path):
    db = tmp_path / "retention.sqlite"
    source = node(NodeType.REVIEW, "review")
    closed(db, source)
    with pytest.raises(RetentionError, match="retention_referenced"):
        add_edge(source, node(NodeType.EVIDENCE, "missing"), edge_type="evidence",
                 state=EdgeState.CLOSED, created_at=OLD, db_path=db)


def test_unattested_history_stays_unselectable_and_collector_does_not_admit(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "historic-generation")
    closed(db, generation)
    assert historical_generation_selectable(generation, db_path=db) is False
    mark(run_id="history", observed_at=NOW, db_path=db)
    sweep(run_id="history", observed_at=NOW, receipt_key=KEY, db_path=db)
    assert historical_generation_selectable(generation, db_path=db) is False


def test_only_explicit_admission_edge_makes_history_selectable(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "historic-generation")
    receipt = node(NodeType.ADMISSION_RECEIPT, "admission")
    closed(db, generation)
    closed(db, receipt)
    add_edge(receipt, generation, edge_type="admits", state=EdgeState.CLOSED,
             created_at=OLD, db_path=db)
    assert historical_generation_selectable(generation, db_path=db) is True
