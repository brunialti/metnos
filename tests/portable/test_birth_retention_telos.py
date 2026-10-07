"""Collect physical rejected proposals without resurrecting native decisions."""
from dataclasses import replace
import json
import os
import signal
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import plan
from install.birth_retention_jsonl import _TEMP_PREFIX
from install.birth_retention_telos import _TelosDecisions, _TelosProposalOwner
import telos_proposals_store as telos
from test_birth_retention_artifacts import RUN, OBSERVED, collection


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX Telos journals")
OLD = 1577836800.0


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    paths = tuple(root / path.name for path in telos._PROPOSALS_CANDIDATES)
    decisions_path = root / "telos_decisions.jsonl"
    monkeypatch.setattr(telos, "_DATA_DIR", root)
    monkeypatch.setattr(telos, "_PROPOSALS_CANDIDATES", paths)
    monkeypatch.setattr(telos, "DECISIONS_PATH", decisions_path)
    monkeypatch.setattr(telos.time, "time", lambda: OLD + 100)
    decisions = _TelosDecisions(path=decisions_path, require_exclusion=lambda: None, owner=None)
    owner = _TelosProposalOwner(paths=paths, decisions=decisions)

    def append(index, ts=OLD, **changes):
        record = dict(ts=ts, executor_target="same-target", expected_alignment=.6,
                      rationale="native proposal", lens="test")
        record.update(changes)
        raw = (json.dumps(record, ensure_ascii=False) + "\n").encode()
        with paths[index].open("ab") as stream:
            stream.write(raw)
        paths[index].chmod(0o600)
        return raw

    def decide(ts=OLD, action="reject", **changes):
        previous_umask = os.umask(0o077)  # The service writer's native UMask.
        try:
            return telos.apply_decision(telos._format_prop_id(ts), action, run_on_accept=False,
                                       executor_target="same-target", signature_relaxed="signature", **changes)
        finally:
            os.umask(previous_umask)

    return SimpleNamespace(paths=paths, decisions=decisions, owner=owner,
                           append=append, decide=decide, monkeypatch=monkeypatch)


def candidates(native, tmp_path, *, holds=frozenset()):
    return plan(native.owner.inventory(), observed_owners=frozenset(native.owner.owners),
                required_owners=frozenset(native.owner.owners), observed_roots=frozenset(RootKind),
                holds=holds, graph_path=tmp_path / "projection.sqlite", run_id=RUN, observed_at=OBSERVED)


def test_signed_collection_removes_all_rejected_copies_preserving_native_priority_and_tombstone(native, tmp_path):
    remaining = []
    for index in range(3):
        native.append(index, expected_alignment=.1 * index)
        remaining.append(native.append(index, OLD + 1, expected_alignment=.9 - .1 * index))
        native.append(index, rationale="duplicate physical row")
    native.decide()
    before = telos.load_all(include_decided=False)
    decisions = telos.decisions_index()
    rejection = telos.rejected_targets(), telos.rejected_signatures_relaxed()
    selected = candidates(native, tmp_path)
    assert len(selected) == 3
    assert len({obj.identity.store for obj in selected}) == 3
    root, public, factory = collection(tmp_path, selected, native.owner.owners)
    assert factory().resume(native.owner.owners, public_keys=public, max_objects=20,
                            max_seconds=20)["remaining"] == 0
    factory().finish(native.owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert [path.read_bytes() for path in native.paths] == remaining
    assert telos.load_all(include_decided=False) == before
    assert telos.decisions_index() == decisions
    assert (telos.rejected_targets(), telos.rejected_signatures_relaxed()) == rejection
    assert not (root / "active.json").exists()


@pytest.mark.parametrize("actions", [[], ["stage"], ["accept"], ["accept", "reject"], ["reject", "stage"]])
def test_unfinished_or_previously_accepted_proposal_cannot_be_collected(native, tmp_path, actions):
    native.append(0)
    for action in actions:
        native.decide(action=action)
    obj = native.owner.scan()[0].object
    assert obj.state is NodeState.OPEN
    assert not candidates(native, tmp_path)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.owner.delete(obj.identity, obj.version)


def test_physical_last_decision_wins_even_when_its_timestamp_is_older(native, tmp_path):
    native.append(0)
    native.decide(action="stage")
    native.monkeypatch.setattr(telos.time, "time", lambda: OLD - 1)
    native.decide()
    assert len(candidates(native, tmp_path)) == 1
    assert telos.decisions_index()[telos._format_prop_id(OLD)]["action"] == "reject"


def test_hold_on_one_copy_retains_all_copies_but_not_same_named_other_proposal(native, tmp_path):
    for index in range(3):
        native.append(index)
        native.append(index, OLD + 1)
    native.decide()
    native.decide(OLD + 1)
    held = native.owner.scan()[0].object.identity
    selected = candidates(native, tmp_path, holds=frozenset({held.key.node_id}))
    assert len(selected) == 3
    assert all(obj.identity.local_id == telos._format_prop_id(OLD + 1) for obj in selected)


def test_microsecond_collision_groups_all_physical_rows(native, tmp_path):
    native.append(0, 1.0000001)
    native.append(0, 1.0000002)
    native.decide(1.0000001)
    entry, = native.owner.scan()
    assert len(entry.records) == 2
    assert len(candidates(native, tmp_path)) == 1
    native.owner.delete(entry.object.identity, entry.object.version)
    assert native.paths[0].read_bytes() == b""


def test_new_stage_after_inventory_blocks_effect_with_unchanged_proposal_bytes(native):
    native.append(0)
    native.decide()
    obj = native.owner.scan()[0].object
    original = native.paths[0].read_bytes()
    native.decide(action="stage")
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.owner.delete(obj.identity, obj.version)
    assert native.paths[0].read_bytes() == original


def test_unexpired_rejection_is_not_a_candidate(native, tmp_path):
    native.append(0)
    native.monkeypatch.setattr(telos.time, "time", lambda: 1791158400.0)
    native.decide()
    assert not candidates(native, tmp_path)


def test_orphan_decision_remains_native_state(native, tmp_path):
    native.decide()
    assert not candidates(native, tmp_path)
    entry, = native.decisions.scan()
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.decisions.delete(entry.object.identity, entry.object.version)
    assert telos.rejected_targets() == {"same-target"}


@pytest.mark.parametrize("raw", [b'{"ts":true}\n', b'{"ts":NaN}\n', b'{"ts":1,"ts":2}\n', b'{"ts":1}'])
def test_malformed_physical_proposal_blocks_inventory(native, raw):
    native.paths[0].write_bytes(raw)
    native.paths[0].chmod(0o600)
    with pytest.raises(RetentionError):
        native.owner.inventory()


def test_decisions_changed_during_inventory_are_rejected(native, monkeypatch):
    native.append(0)
    native.decide()
    original = native.decisions.scan
    count = 0
    def changed():
        nonlocal count
        count += 1
        if count == 2:
            native.decide(action="stage")
        return original()
    monkeypatch.setattr(native.decisions, "scan", changed)
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        native.owner.inventory()


def test_foreign_physical_store_cannot_be_deleted(native):
    native.append(0)
    native.decide()
    obj = native.owner.scan()[0].object
    with pytest.raises(RetentionError, match="retention_owner_invalid"):
        native.owner.delete(replace(obj.identity, store="file:///another/store"), obj.version)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native SIGKILL recovery")
def test_recovery_with_proposal_temporary_can_read_decisions_and_reuse_signed_intents(native, tmp_path):
    remaining = []
    for index in range(3):
        native.append(index)
        remaining.append(native.append(index, OLD + 1))
    native.decide()
    selected = candidates(native, tmp_path)
    root, public, factory = collection(tmp_path, selected, native.owner.owners)
    journal = root / (RUN[7:] + ".sqlite")
    with sqlite3.connect(journal) as db:
        original = db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall()
    rename = os.replace
    child = os.fork()
    if child == 0:
        try:
            def interrupted(source, destination, **kwargs):
                if str(source).startswith(_TEMP_PREFIX):
                    os.kill(os.getpid(), signal.SIGKILL)
                return rename(source, destination, **kwargs)
            os.replace = interrupted
            factory().resume(native.owner.owners, public_keys=public, max_objects=20, max_seconds=20)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        native.owner.inventory()
    assert factory().resume(native.owner.owners, public_keys=public, max_objects=20,
                            max_seconds=20)["remaining"] == 0
    with sqlite3.connect(journal) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == original
    factory().finish(native.owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert [path.read_bytes() for path in native.paths] == remaining
    assert telos.rejected_targets() == {"same-target"}
