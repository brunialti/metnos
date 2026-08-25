import json
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_retention import (
    CandidateStatus, EdgeState, EdgeType, NodeKey, NodeState, NodeType,
    RetentionError, RootKind,
    add_edge, add_root, close_edge, historical_generation_selectable, mark,
    put_node, remove_root, sweep, verify_minimal_receipt,
)


OLD = "2026-01-01T00:00:00Z"
NOW = "2026-08-25T12:00:00Z"
FUTURE = "2027-01-01T00:00:00Z"
KEY = Ed25519PrivateKey.from_private_bytes(b"k" * 32)


def run_sweep(*, run_id, observed_at, db_path, **kwargs):
    kwargs.setdefault("delete_object", lambda _key, _guard: None)
    return sweep(run_id=run_id, observed_at=observed_at,
                 receipt_key_id="retention-2026", receipt_private_key=KEY,
                 receipt_public_keys={"retention-2026": KEY.public_key()},
                 db_path=db_path, **kwargs)


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
    review = node(NodeType.REVISION, "sha256:" + "d" * 64)
    evidence = node(NodeType.EVIDENCE, "sha256:" + "e" * 64)
    approval = node(NodeType.APPROVAL, "sha256:" + "f" * 64)
    report = node(NodeType.BIRTH_REPORT, "sha256:" + "1" * 64)
    orphan = node(NodeType.EVIDENCE, "sha256:" + "2" * 64)
    for key in (generation, admission, producer, review, evidence, approval, report, orphan):
        closed(db, key)
    add_root(generation, root_kind=RootKind.CURRENT_POINTER, db_path=db)
    for source, target, relation in (
        (generation, admission, EdgeType.ADMITS), (admission, producer, EdgeType.PROVENANCE),
        (admission, review, EdgeType.REVISION), (review, evidence, EdgeType.EVIDENCE),
        (admission, approval, EdgeType.APPROVAL), (admission, report, EdgeType.AUDIT_REFERENCE),
    ):
        add_edge(source, target, edge_type=relation, state=EdgeState.CLOSED,
                 created_at=OLD, db_path=db)
    assert mark(run_id="run-1", observed_at=NOW, db_path=db) == (orphan,)
    result = run_sweep(run_id="run-1", observed_at=NOW, db_path=db)
    assert result.deleted == (orphan,)


def test_open_reference_is_conservative_root(tmp_path):
    db = tmp_path / "retention.sqlite"
    report = node(NodeType.BIRTH_REPORT, "report")
    evidence = node(NodeType.EVIDENCE, "evidence")
    closed(db, report)
    closed(db, evidence)
    add_edge(report, evidence, edge_type=EdgeType.EVIDENCE, state=EdgeState.OPEN,
             created_at=OLD, db_path=db)
    assert mark(run_id="run-open", observed_at=NOW, db_path=db) == ()
    close_edge(report, evidence, edge_type=EdgeType.EVIDENCE, closed_at=NOW, db_path=db)
    assert set(mark(run_id="run-closed", observed_at=NOW, db_path=db)) == {report, evidence}


def test_removed_pointer_stops_being_a_root(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "old-current")
    closed(db, generation)
    add_root(generation, root_kind=RootKind.CURRENT_POINTER, db_path=db)
    assert mark(run_id="rooted", observed_at=NOW, db_path=db) == ()
    remove_root(generation, root_kind=RootKind.CURRENT_POINTER, db_path=db)
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
            add_root(key, root_kind=RootKind.ADMITTED_ROLLBACKABLE, db_path=db)

    result = run_sweep(run_id="race", observed_at=NOW,
                       db_path=db, before_each=race,
                       delete_object=lambda _key, _guard: None)
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
    result = run_sweep(run_id="versions", observed_at=NOW, db_path=db)
    assert dict(result.preserved) == {
        changed: CandidateStatus.STATE_CHANGED,
        window: CandidateStatus.STATE_CHANGED,
    }


def test_double_sweep_is_idempotent_and_receipt_is_unique(tmp_path):
    db = tmp_path / "retention.sqlite"
    key = node(NodeType.EVIDENCE, "once")
    closed(db, key)
    mark(run_id="double", observed_at=NOW, db_path=db)
    first = run_sweep(run_id="double", observed_at=NOW, db_path=db)
    second = run_sweep(run_id="double", observed_at=NOW, db_path=db)
    assert first.deleted == (key,)
    assert second.deleted == ()
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT count(*) FROM retention_receipts").fetchone()[0] == 1


def test_sweep_deletes_referenced_leaf_before_owner(tmp_path):
    db = tmp_path / "retention.sqlite"
    owner = node(NodeType.BIRTH_REPORT, "owner")
    leaf = node(NodeType.EVIDENCE, "leaf")
    closed(db, owner)
    closed(db, leaf)
    add_edge(owner, leaf, edge_type=EdgeType.EVIDENCE, state=EdgeState.CLOSED,
             created_at=OLD, db_path=db)
    mark(run_id="leaves", observed_at=NOW, db_path=db)
    result = run_sweep(run_id="leaves", observed_at=NOW, db_path=db)
    assert result.deleted == (leaf, owner)


def test_missing_edge_endpoint_is_rejected_atomically(tmp_path):
    db = tmp_path / "retention.sqlite"
    source = node(NodeType.REVISION, "review")
    closed(db, source)
    with pytest.raises(RetentionError, match="retention_referenced"):
        add_edge(source, node(NodeType.EVIDENCE, "missing"), edge_type=EdgeType.EVIDENCE,
                 state=EdgeState.CLOSED, created_at=OLD, db_path=db)


def test_unattested_history_stays_unselectable_and_collector_does_not_admit(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "historic-generation")
    closed(db, generation)
    assert historical_generation_selectable(generation, db_path=db) is False
    mark(run_id="history", observed_at=NOW, db_path=db)
    run_sweep(run_id="history", observed_at=NOW, db_path=db,
              delete_object=lambda _key, _guard: None)
    assert historical_generation_selectable(generation, db_path=db) is False


def test_only_explicit_admission_edge_makes_history_selectable(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "historic-generation")
    receipt = node(NodeType.ADMISSION_RECEIPT, "admission")
    closed(db, generation)
    closed(db, receipt)
    add_edge(receipt, generation, edge_type=EdgeType.ADMITS, state=EdgeState.CLOSED,
             created_at=OLD, db_path=db)
    assert historical_generation_selectable(generation, db_path=db) is True


def test_closed_taxonomy_covers_every_normative_graph_artifact_and_relation():
    assert {item.value for item in NodeType} == {
        "generation", "retirement", "birth_report", "admission_receipt",
        "producer_receipt", "minimal_receipt", "candidate_copy", "proposal",
        "blob", "evidence", "approval", "feedback", "revision", "epoch",
        "audit_segment", "job",
    }
    assert {item.value for item in EdgeType} == {
        "selects", "predecessor", "admits", "provenance", "evidence",
        "approval", "execution", "revision", "repair",
        "rollback_destination", "audit_reference",
    }
    assert {item.value for item in RootKind} == {
        "current_pointer", "retirement_predecessor", "admitted_rollbackable",
        "open_feedback", "open_revision", "open_approval", "open_audit",
        "in_progress_job", "current_epoch", "unexpired_window", "legal_hold",
    }


def test_raw_relation_and_root_strings_are_rejected(tmp_path):
    db = tmp_path / "retention.sqlite"
    one, two = node(NodeType.EVIDENCE, "one"), node(NodeType.BLOB, "two")
    closed(db, one)
    closed(db, two)
    with pytest.raises(RetentionError, match="retention_invalid"):
        add_edge(one, two, edge_type="evidence", state=EdgeState.CLOSED,  # type: ignore[arg-type]
                 created_at=OLD, db_path=db)
    with pytest.raises(RetentionError, match="retention_invalid"):
        add_root(one, root_kind="current_pointer", db_path=db)  # type: ignore[arg-type]


def test_minimal_receipt_is_ed25519_authenticated_and_tamper_evident(tmp_path):
    db = tmp_path / "retention.sqlite"
    key = node(NodeType.EVIDENCE, "signed")
    closed(db, key)
    mark(run_id="signed-run", observed_at=NOW, db_path=db)
    run_sweep(run_id="signed-run", observed_at=NOW, db_path=db)
    connection = sqlite3.connect(db)
    row = connection.execute(
        "SELECT object_version,deleted_at,authentication FROM retention_receipts"
    ).fetchone()
    connection.close()
    assert json.loads(row[2]) == {
        "algorithm": "ed25519", "key_id": "retention-2026",
        "schema_version": 1,
        "signature": json.loads(row[2])["signature"],
    }
    assert verify_minimal_receipt(
        key=key, run_id="signed-run", object_version=row[0], deleted_at=row[1],
        authentication=row[2], public_keys={"retention-2026": KEY.public_key()},
    ) == "retention-2026"
    with pytest.raises(RetentionError, match="minimal receipt authentication"):
        verify_minimal_receipt(
            key=key, run_id="different-run", object_version=row[0],
            deleted_at=row[1], authentication=row[2],
            public_keys={"retention-2026": KEY.public_key()},
        )
    envelope = json.loads(row[2])
    envelope["unexpected"] = False
    with pytest.raises(RetentionError, match="minimal receipt authentication"):
        verify_minimal_receipt(
            key=key, run_id="signed-run", object_version=row[0], deleted_at=row[1],
            authentication=json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            public_keys={"retention-2026": KEY.public_key()},
        )


def test_callback_crash_resumes_same_object_and_freezes_new_references(tmp_path):
    db = tmp_path / "retention.sqlite"
    key = node(NodeType.EVIDENCE, "crash-resume")
    closed(db, key)
    mark(run_id="resume", observed_at=NOW, db_path=db)
    attempts = 0

    def crash_once(_key, _guard):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated crash boundary")

    with pytest.raises(RetentionError, match="retention_partial"):
        run_sweep(run_id="resume", observed_at=NOW, db_path=db,
                  delete_object=crash_once)
    with pytest.raises(RetentionError, match="retention_state_changed"):
        add_root(key, root_kind=RootKind.LEGAL_HOLD, db_path=db)
    result = run_sweep(run_id="resume", observed_at=FUTURE, db_path=db,
                       delete_object=crash_once)
    assert result.deleted == (key,)
    assert attempts == 2
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT count(*) FROM retention_receipts").fetchone()[0] == 1
    connection.close()


def test_resume_rejects_tampered_persisted_receipt(tmp_path):
    db = tmp_path / "retention.sqlite"
    key = node(NodeType.EVIDENCE, "tampered-resume")
    closed(db, key)
    mark(run_id="tampered", observed_at=NOW, db_path=db)
    with pytest.raises(RetentionError, match="retention_partial"):
        run_sweep(
            run_id="tampered", observed_at=NOW, db_path=db,
            delete_object=lambda *_: (_ for _ in ()).throw(OSError("crash")),
        )
    connection = sqlite3.connect(db)
    connection.execute(
        "UPDATE retention_receipts SET authentication=?",
        (json.dumps({"algorithm": "ed25519", "key_id": "retention-2026",
                     "schema_version": 1, "signature": "AAAA"},
                    sort_keys=True, separators=(",", ":")),),
    )
    connection.commit()
    connection.close()
    with pytest.raises(RetentionError, match="minimal receipt authentication"):
        run_sweep(run_id="tampered", observed_at=NOW, db_path=db,
                  delete_object=lambda *_: None)


def test_generation_callback_receives_strict_guard_and_is_mandatory(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "unreferenced-history")
    closed(db, generation)
    mark(run_id="generation-delete", observed_at=NOW, db_path=db)
    with pytest.raises(RetentionError, match="delete callback absent"):
        sweep(run_id="generation-delete", observed_at=NOW,
              receipt_key_id="retention-2026", receipt_private_key=KEY,
              receipt_public_keys={"retention-2026": KEY.public_key()},
              db_path=db, delete_object=None)
    observed = []
    result = run_sweep(
        run_id="generation-delete", observed_at=NOW, db_path=db,
        delete_object=lambda key, guard: observed.append((key, guard)),
    )
    assert result.deleted == (generation,)
    guard = observed[0][1]
    assert guard is not None
    assert (guard.noncurrent, guard.no_retirement_requirement,
            guard.not_rollbackable, guard.unreferenced) == (True, True, True, True)


def test_generation_with_any_closed_reference_is_never_given_to_callback(tmp_path):
    db = tmp_path / "retention.sqlite"
    generation = node(NodeType.GENERATION, "referenced-history")
    retirement = node(NodeType.RETIREMENT, "retirement")
    closed(db, generation)
    closed(db, retirement)
    add_edge(retirement, generation, edge_type=EdgeType.PREDECESSOR,
             state=EdgeState.CLOSED, created_at=OLD, db_path=db)
    mark(run_id="referenced-generation", observed_at=NOW, db_path=db)
    seen = []
    result = run_sweep(
        run_id="referenced-generation", observed_at=NOW, db_path=db,
        delete_object=lambda key, guard: seen.append((key, guard)),
    )
    assert generation not in result.deleted
    assert (generation, CandidateStatus.REFERENCED) in result.preserved
    assert all(key != generation for key, _guard in seen)


def test_generic_object_requires_deletion_owner_before_receipt_or_tombstone(tmp_path):
    db = tmp_path / "retention.sqlite"
    key = node(NodeType.BLOB, "physical-blob")
    closed(db, key)
    mark(run_id="no-owner", observed_at=NOW, db_path=db)
    with pytest.raises(RetentionError, match="delete callback absent"):
        sweep(run_id="no-owner", observed_at=NOW,
              receipt_key_id="retention-2026", receipt_private_key=KEY,
              receipt_public_keys={"retention-2026": KEY.public_key()},
              db_path=db, delete_object=None)
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT count(*) FROM retention_receipts").fetchone()[0] == 0
    assert connection.execute(
        "SELECT state FROM retention_nodes WHERE node_type='blob' AND node_id='physical-blob'"
    ).fetchone()[0] == "closed"
    connection.close()


def test_crash_resume_verifies_historical_receipt_after_key_rotation(tmp_path):
    db = tmp_path / "retention.sqlite"
    old_key = Ed25519PrivateKey.from_private_bytes(b"o" * 32)
    new_key = Ed25519PrivateKey.from_private_bytes(b"n" * 32)
    key = node(NodeType.BLOB, "rotated")
    closed(db, key)
    mark(run_id="rotation", observed_at=NOW, db_path=db)

    with pytest.raises(RetentionError, match="retention_partial"):
        sweep(
            run_id="rotation", observed_at=NOW, receipt_key_id="retention-old",
            receipt_private_key=old_key,
            receipt_public_keys={"retention-old": old_key.public_key()}, db_path=db,
            delete_object=lambda *_: (_ for _ in ()).throw(OSError("crash")),
        )
    result = sweep(
        run_id="rotation", observed_at=FUTURE, receipt_key_id="retention-new",
        receipt_private_key=new_key,
        receipt_public_keys={"retention-old": old_key.public_key(),
                             "retention-new": new_key.public_key()},
        db_path=db, delete_object=lambda *_: None,
    )
    assert result.deleted == (key,)
    connection = sqlite3.connect(db)
    envelope = json.loads(connection.execute(
        "SELECT authentication FROM retention_receipts"
    ).fetchone()[0])
    connection.close()
    assert envelope["key_id"] == "retention-old"
