"""Native journal references and signed collection; not installed F6 acceptance."""
from dataclasses import replace
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

import turn_feedback
from executor_birth_failure_review import _canonical
from executor_birth_retention import RetentionError, RootKind
from install.birth_retention_journals import _JournalInventory
from install.birth_retention_history import _HistoryBackupOwner
from install.birth_retention_maintenance import plan
from test_birth_retention_artifacts import OBSERVED, RUN, collection
from test_birth_retention_feedback import native as feedback_fixture, record as feedback_record
from test_birth_retention_protected_undo import secured as secured_fixture, SECRET
from test_birth_retention_turn_archives import archived as archive_fixture
from test_birth_retention_turns import native as turns_fixture, record, DAY
from test_birth_retention_undo import native as undo_fixture


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX journal custody")


@pytest.fixture
def native(tmp_path, monkeypatch):
    # Reuse native writer fixtures, with separate data roots where required.
    feedback = feedback_fixture.__wrapped__(tmp_path, monkeypatch)
    undo_root = tmp_path / "undo"
    undo_root.mkdir()
    undo = undo_fixture.__wrapped__(undo_root, monkeypatch)
    secured = secured_fixture.__wrapped__(undo, monkeypatch)
    turns = turns_fixture.__wrapped__(tmp_path, monkeypatch)
    archives = archive_fixture.__wrapped__(turns, tmp_path)
    inventory = _JournalInventory(turns=turns.owner, archives=archives.owner,
                                  feedback=feedback.owner, protected_undo=secured.owner)
    return SimpleNamespace(turns=turns, archives=archives, feedback=feedback,
                           undo=undo, secured=secured, inventory=inventory)


def candidates(native, tmp_path, *, holds=frozenset()):
    # Explicit fixture subset: it does not attest all installed F6 roots.
    return plan(native.inventory.inventory(), observed_owners=frozenset(native.inventory.owners),
                required_owners=frozenset(native.inventory.owners),
                observed_roots=frozenset(RootKind), holds=holds,
                graph_path=tmp_path / "projection.sqlite", run_id=RUN, observed_at=OBSERVED)


@pytest.mark.parametrize("legacy", [False, True])
def test_history_and_protected_undo_references_are_merged_without_losing_turn(native, monkeypatch, legacy):
    from executor_helpers import backup_file_for_undo
    root = native.undo.path.parent / "_history"
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(root))
    monkeypatch.setenv("METNOS_TURN_ID", "reversible")
    source = native.undo.path.parent / "source.txt"
    source.write_bytes(b"native history backup")
    source.chmod(0o644)
    old_umask = os.umask(0o077)
    try:
        path = backup_file_for_undo(source)
    finally:
        os.umask(old_umask)
    handle, _ = native.secured.create()
    native.undo.log.append_pending("reversible", "reversible", "compute", {}, {})
    native.undo.log.append_done("reversible", {"results": [{"blob_path": path, "secret": handle}]})
    native.turns.append(record("reversible"))
    # A separately retained turn can reference the physical backup even when
    # its own undo operation no longer exists in the native journal.
    reference = {"blob_sha256": path.rsplit("/", 1)[-1][:-4]} if legacy else {
        "blob_path": path}
    native.turns.append(record("audit", outcome="awaiting_input", recovery_evidence=reference))
    history = _HistoryBackupOwner(root=root, journal=native.undo.owner)
    joined = _JournalInventory(turns=native.turns.owner, archives=native.archives.owner,
                              feedback=native.feedback.owner, protected_undo=native.secured.owner,
                              history=history)
    observed = joined.inventory()
    operation = next(obj for obj in observed if obj.identity == native.undo.owner.identity("reversible"))
    assert {ref.owner for ref in operation.references} == {
        "turn_logs", "history_backup_blobs", "protected_undo_blobs"}
    assert history.name in joined.owners
    audit = next(obj for obj in observed if obj.identity == native.turns.owner.identity(DAY, "audit"))
    assert {ref.owner for ref in audit.references} == {"history_backup_blobs"}


def test_signed_collection_across_native_journals_preserves_readers_and_secret_links(native, tmp_path):
    for turn in ("expired", "boundary", "reversible", "recent", "parent"):
        native.turns.append(record(turn))
    native.turns.append(record("child", parent_turn_id="parent", outcome="awaiting_input"))
    native.undo.add("expired")
    handle, secret_path = native.secured.create()
    native.secured.attach(handle, "reversible")
    native.undo.add("boundary")
    native.feedback.append(feedback_record("expired"))
    for _ in range(turn_feedback.FEEDBACK_LOOKBACK):
        native.feedback.append(feedback_record("recent"))
    rejected = turn_feedback.rejected_pipelines_for_query("query")
    boundary = native.undo.log.latest_turn_done(actor="host")
    selected = candidates(native, tmp_path)
    assert {obj.identity.owner for obj in selected} == {"turn_logs", "turn_feedback", "undo_operations"}
    assert all(obj.identity.local_id == "expired" for obj in selected)
    protected_id = native.secured.owner.identity(handle)
    operation = next(obj for obj in native.inventory.inventory()
                     if obj.identity == native.undo.owner.identity("reversible"))
    assert protected_id in operation.references
    root, public, factory = collection(tmp_path, selected, native.inventory.owners)
    assert factory().resume(native.inventory.owners, public_keys=public,
                            max_objects=100, max_seconds=20)["remaining"] == 0
    factory().finish(native.inventory.owners, public_keys=public,
                     verify_recovery=native.inventory.inventory)
    assert native.turns.load("expired") is None
    assert native.turns.load("parent")["turn_id"] == "parent"
    assert native.undo.log.latest_turn_done(actor="host") == boundary
    assert turn_feedback.rejected_pipelines_for_query("query") == rejected
    assert secret_path.exists() and SECRET not in secret_path.read_bytes()
    assert not (root / "active.json").exists()


@pytest.mark.parametrize("root_kind", ["feedback", "undo", "copy", "hold", "review"])
def test_retained_turn_preserves_every_physical_copy_and_gzip_cotenant(native, tmp_path, root_kind):
    archive = native.archives.create([record("kept"), record("cotenant", parent_turn_id="parent")])
    for turn in ("kept", "cotenant", "parent", "unrelated"):
        native.turns.append(record(turn))
    native.turns.append(record("kept", outcome="awaiting_input" if root_kind == "copy" else "completed"),
                        day=DAY + ".bak")
    holds = frozenset()
    if root_kind == "feedback":
        native.feedback.append(feedback_record("kept"))
    elif root_kind == "undo":
        native.undo.add("operation", "reversible", turn="kept")
    elif root_kind == "hold":
        holds = frozenset({native.archives.owner.identity("2020/01/" + DAY + ".gz").key.node_id})
    elif root_kind == "review":
        native.feedback.quarantine("kept")
    selected = candidates(native, tmp_path, holds=holds)
    assert [(obj.identity.owner, obj.identity.local_id) for obj in selected] == [("turn_logs", "unrelated")]
    assert archive.exists()


def test_expired_copy_cycle_is_collected_as_one_recoverable_group(native, tmp_path):
    archive = native.archives.create([record("expired")])
    for day in (DAY, DAY + ".bak"):
        native.turns.append(record("expired"), day=day)
    selected = candidates(native, tmp_path)
    assert len(selected) == 3
    _, public, factory = collection(tmp_path, selected, native.inventory.owners)
    assert factory().resume(native.inventory.owners, public_keys=public,
                            max_objects=10, max_seconds=20)["completed"] == 3
    factory().finish(native.inventory.owners, public_keys=public,
                     verify_recovery=native.inventory.inventory)
    assert not archive.exists() and native.turns.load("expired") is None


def test_native_feedback_without_turn_is_not_an_unresolved_reference(native, tmp_path):
    native.feedback.append(feedback_record("missing", warning="turn_not_found"))
    native.turns.append(record("failed"))
    native.feedback.append(feedback_record("failed", action="error"))
    previous = os.umask(0o077)
    try:
        assert turn_feedback.reset_rejected_for_query("query") == 1
    finally:
        os.umask(previous)
    assert native.inventory.inventory()
    assert not candidates(native, tmp_path)
    assert turn_feedback.rejected_pipelines_for_query("query") == []


@pytest.mark.parametrize("source", ["parent", "undo", "feedback", "review"])
def test_missing_required_turn_blocks_before_any_effect(native, source):
    if source == "parent":
        native.turns.append(record("child", parent_turn_id="missing"))
    elif source == "undo":
        native.undo.add("operation", turn="missing")
    elif source == "feedback":
        native.feedback.append(feedback_record("missing"))
    else:
        native.feedback.quarantine("missing")
        # Same executor is not the exact receipt requested by the queue.
        native.turns.append(record("another"))
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        native.inventory.inventory()


@pytest.mark.parametrize("field", ["job_id", "generation_id", "execution_receipt_hash", "reduced_arguments"])
def test_pending_review_requires_exact_native_evidence(native, field):
    native.turns.append(record("turn"))
    native.feedback.quarantine("turn")
    with sqlite3.connect(native.feedback.reviews.path) as connection:
        if field == "job_id":
            connection.execute("UPDATE executor_failure_review_queue SET job_id=?", ("sha256:" + "9" * 64,))
        else:
            raw, = connection.execute("SELECT request_json FROM executor_failure_review_queue").fetchone()
            value = json.loads(raw)
            value[field] = {} if field == "reduced_arguments" else "sha256:" + "9" * 64
            encoded = _canonical(value)
            connection.execute("UPDATE executor_failure_review_queue SET request_json=?", (encoded,))
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        native.inventory.inventory()


@pytest.mark.parametrize("damage", ["missing", "changed", "duplicate", "foreign", "dangling", "reread"])
def test_inconsistent_owner_projection_blocks_collection(native, monkeypatch, damage):
    native.turns.append(record("turn"))
    native.undo.add("turn")
    original = native.secured.owner.inventory
    calls = 0
    def altered():
        nonlocal calls
        calls += 1
        objects = original()
        if damage == "reread" and calls == 1:
            return objects
        if damage == "missing":
            return ()
        if damage == "duplicate":
            return objects + objects
        obj, = objects
        if damage in {"changed", "reread"}:
            return (replace(obj, version="sha256:" + "9" * 64),)
        if damage == "foreign":
            return (replace(obj, identity=replace(obj.identity, owner="foreign")),)
        return (replace(obj, references=(native.secured.owner.identity("0" * 64),)),)
    monkeypatch.setattr(native.secured.owner, "inventory", altered)
    with pytest.raises(RetentionError, match="retention_(inventory_incomplete|owner_changed)"):
        native.inventory.inventory()
    assert native.turns.load("turn") is not None
