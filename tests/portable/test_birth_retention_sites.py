"""Native audit writer/reader, session closure, physical copies and signed recovery."""
from datetime import datetime, timezone
import json
import os
from unittest.mock import patch
from uuid import uuid4

import pytest

import sites_audit
from credential_mandates import verified_site_topology
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_sites import _SitesAuditOwner
from test_birth_retention_llm_cost import selected
from test_birth_retention_artifacts import collection

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX audit custody")
OLD = "2020-01-01T00:00:00Z"


@pytest.fixture
def native(tmp_path, monkeypatch):
    path = tmp_path / "audit" / "sites_audit.jsonl"
    monkeypatch.setattr(sites_audit, "AUDIT_PATH", path)
    owner = _SitesAuditOwner(path=path, require_exclusion=lambda: None, owner=None)

    def emit(event, *, timestamp=OLD, sid="session", **fields):
        with patch.object(sites_audit.time, "strftime", return_value=timestamp):
            sites_audit.record(event, owner="actor", domain="example.com", session_id=sid, **fields)
        assert path.exists()

    return path, owner, emit


def collect(owner, tmp_path, holds=frozenset()):
    root = tmp_path / uuid4().hex
    root.mkdir()
    chosen = selected(owner, root, holds)
    if chosen:
        _, public, factory = collection(root, chosen, owner.owners)
        factory().resume(owner.owners, public_keys=public, max_objects=100, max_seconds=20)
        factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    return chosen


def test_native_topology_unchanged_and_closure_collected_after_history(native, tmp_path):
    path, owner, emit = native
    emit("session_open", owner_user_id="logical-owner", allowlist=["example.com", "www.example.com"])
    emit("allowlist_change", owner_user_id="logical-owner", source="approved_action", added_host="login.example.com")
    emit("credential_origin_approval", owner_user_id="logical-owner", origin="id.example.com", outcome=True)
    emit("login_attempt", outcome=True)
    emit("session_close")
    topology = verified_site_topology("logical-owner", audit_path=path)
    original = path.read_bytes().splitlines(keepends=True)
    assert topology["example.com"]["origins"] == {"id.example.com"}
    assert len(collect(owner, tmp_path)) == 1
    assert path.read_bytes() == b"".join(original[:3] + original[4:])
    assert len(collect(owner, tmp_path)) == 1
    assert path.read_bytes() == b"".join(original[:3])
    assert verified_site_topology("logical-owner", audit_path=path) == topology
    assert collect(owner, tmp_path) == ()


def test_any_owner_bound_event_remains_for_native_domain_selection(native, tmp_path):
    path, owner, emit = native
    emit("login_attempt", owner_user_id="logical-owner", outcome=True)
    emit("session_close")
    original = path.read_bytes().splitlines(keepends=True)[0]
    assert len(collect(owner, tmp_path)) == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize("condition", ["active", "no_session", "recent", "after_close"])
def test_unclosed_and_recent_audits_cannot_be_deleted(native, tmp_path, condition):
    path, owner, emit = native
    if condition == "no_session":
        emit("stealth_denied_by_ceiling", sid="")
    elif condition == "after_close":
        emit("session_close")
        emit("login_attempt", timestamp="2020-01-02T00:00:00Z", outcome=True)
    else:
        emit("login_attempt", outcome=True)
        if condition == "recent":
            emit("session_close", timestamp=datetime.now(timezone.utc).isoformat())
    original = path.read_bytes()
    assert selected(owner, tmp_path) == ()
    for obj in owner.inventory():
        with pytest.raises(RetentionError):
            owner.delete(obj.identity, obj.version)
    assert path.read_bytes() == original


def test_rotated_copies_follow_hold_and_closure_survives_both(native, tmp_path):
    path, owner, emit = native
    emit("login_attempt", outcome=True)
    legacy = path.with_name(path.name + ".1")
    legacy.write_bytes(path.read_bytes())
    legacy.chmod(0o600)
    emit("session_close")
    objects = owner.inventory()
    history = [obj for obj in objects if obj.state is NodeState.CLOSED]
    assert len(history) == 2
    assert selected(owner, tmp_path, frozenset({history[0].identity.key.node_id})) == ()
    assert len(collect(owner, tmp_path)) == 2
    assert legacy.read_bytes() == b""
    assert json.loads(path.read_text())["event"] == "session_close"
    assert len(collect(owner, tmp_path)) == 1
    assert path.read_bytes() == b""


@pytest.mark.parametrize("damage", ["event", "partial", "reference", "ambiguous", "symlink"])
def test_unreconciled_audit_blocks_inventory(native, damage):
    path, owner, emit = native
    emit("login_attempt", outcome=True)
    if damage == "partial":
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
    elif damage == "symlink":
        path.with_name(path.name + ".1").symlink_to(path)
    elif damage == "ambiguous":
        record = json.loads(path.read_text())
        record["domain"] = "other.example"
        with path.open("a") as stream:
            stream.write(json.dumps(record) + "\n")
    else:
        record = json.loads(path.read_text())
        record["event" if damage == "event" else "job_id"] = "unknown"
        path.write_text(json.dumps(record) + "\n")
    with pytest.raises(RetentionError):
        owner.inventory()


def test_lost_closure_blocks_previously_selected_history(native, tmp_path):
    path, owner, emit = native
    emit("login_attempt", outcome=True)
    emit("session_close")
    chosen = selected(owner, tmp_path)
    assert len(chosen) == 1
    history = next(obj for obj in owner.inventory() if obj.state is NodeState.CLOSED)
    path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
    with pytest.raises(RetentionError):
        owner.delete(history.identity, history.version)
    assert json.loads(path.read_text())["event"] == "login_attempt"


def test_signed_recovery_keeps_witness_after_interrupted_compaction(native, tmp_path, monkeypatch):
    path, owner, emit = native
    emit("login_attempt", outcome=True)
    emit("session_reap")
    chosen = selected(owner, tmp_path)
    _, public, factory = collection(tmp_path, chosen, owner.owners)
    import install.birth_retention_jsonl as journal
    original = journal.os.replace

    def interrupt(*args, **kwargs):
        raise OSError("injected before replace")

    monkeypatch.setattr(journal.os, "replace", interrupt)
    with pytest.raises(RetentionError):
        factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    with pytest.raises(RetentionError):
        owner.inventory()
    monkeypatch.setattr(journal.os, "replace", original)
    assert factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert json.loads(path.read_text())["event"] == "session_reap"
    assert len(collect(owner, tmp_path)) == 1
    assert path.read_bytes() == b""
