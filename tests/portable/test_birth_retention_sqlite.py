"""Real operational schemas and row effects, including F6 crash recovery."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import signal
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import approval_registry
import change_intents
import proposals_state
from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import Maintenance, OwnerObject
from install.birth_retention_sqlite import (
    _SQLiteOwner, _approval_state, _intent_state, _proposal_state,
)


OLD = "2020-01-01T00:00:00Z"
OLD_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
NOW = "2026-10-05T12:00:00Z"
RUN = "sha256:" + "b" * 64


@pytest.fixture
def approvals(tmp_path, monkeypatch):
    path = tmp_path / "approvals.sqlite"
    monkeypatch.setattr(approval_registry.time, "time", lambda: OLD_EPOCH)
    monkeypatch.setattr(approval_registry, "_now_iso", lambda: OLD)
    allowed = [True]

    def excluded():
        if not allowed[0]:
            raise RetentionError("retention_invalid", "lost exclusion")

    owner = _SQLiteOwner(name="approval_registry", path=path,
                         schema=approval_registry.SCHEMA, table="pending",
                         primary_key="token", node_type="approval", state=_approval_state,
                         require_exclusion=excluded, owner=None)

    def create(status="approved"):
        request = approval_registry.create_pending(
            channel="test", sender_id="operator", capability_class="read",
            action_verb="inspect", target_summary="isolated sample", db_path=path,
        )
        if status != "pending":
            approval_registry.resolve(request.token, status, by_channel="test",
                                      by_sender="operator", db_path=path)
        return request

    return owner, create, allowed


def test_scan_does_not_create_missing_store_or_parent(approvals, tmp_path):
    owner, _, _ = approvals
    owner.path = tmp_path / "not-created" / "approvals.sqlite"
    assert owner.scan() == ()
    assert not owner.path.parent.exists()


def test_orphan_sidecar_is_not_empty_inventory(approvals):
    owner, _, _ = approvals
    Path(str(owner.path) + "-wal").write_bytes(b"unreconciled")
    with pytest.raises(RetentionError, match="retention_owner_path_invalid"):
        owner.scan()
    assert not owner.path.exists()


@pytest.mark.skipif(os.name != "posix", reason="Unix account and parent custody")
def test_service_account_custody_rejects_writable_parent_even_when_store_absent(approvals):
    owner, create, _ = approvals
    # /tmp is outside the service's protected namespace: absence is not an
    # exemption from checking ancestors, and a real store is no exception.
    owner.owner = (os.getuid(), os.getgid())
    with pytest.raises(RetentionError, match="parent custody"):
        owner.scan()
    create()
    with pytest.raises(RetentionError, match="parent custody"):
        owner.scan()


def test_native_approval_closed_window_and_physical_effect(approvals):
    owner, create, _ = approvals
    request = create()
    record, = owner.scan()
    assert record.values["token"] == request.token
    state = owner.state(record.values)
    assert state.state is NodeState.CLOSED
    assert state.eligible_after == "2020-03-31T00:00:00Z"
    assert owner.version(record.identity) == record.version
    owner.delete(record.identity, record.version)
    assert owner.version(record.identity) is None
    owner.delete(record.identity, record.version)  # an interrupted confirmation is repeatable
    assert owner.scan() == ()
    assert approval_registry.get_pending(request.token, db_path=owner.path) is None


def test_expired_pending_requires_native_transition_before_collection(approvals):
    owner, create, _ = approvals
    create("pending")
    row, = owner.scan()
    assert owner.state(row.values).roots == (RootKind.OPEN_APPROVAL,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(row.identity, row.version)
    with sqlite3.connect(owner.path) as db:
        db.execute("UPDATE pending SET expires_at=?", ("2019-12-31T00:00:00Z",))
    assert approval_registry.cleanup_expired(db_path=owner.path) == 1
    expired, = owner.scan()
    assert expired.values["status"] == "expired"
    owner.delete(expired.identity, expired.version)


def test_explicit_validity_cannot_be_shortened(approvals):
    owner, create, _ = approvals
    create()
    with sqlite3.connect(owner.path) as db:
        db.execute("UPDATE pending SET expires_at='2099-01-01T00:00:00Z'")
    row, = owner.scan()
    assert owner.state(row.values).eligible_after == "2099-01-01T00:00:00Z"
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(row.identity, row.version)


def test_all_row_values_bind_the_version_but_other_rows_do_not(approvals):
    owner, create, _ = approvals
    first = create()
    initial, = owner.scan()
    create()
    assert owner.version(initial.identity) == initial.version
    with sqlite3.connect(owner.path) as db:
        db.execute("UPDATE pending SET request_extra=? WHERE token=?",
                   ('{"reference":"new evidence"}', first.token))
    assert owner.version(initial.identity) != initial.version
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        owner.delete(initial.identity, initial.version)
    assert len(owner.scan()) == 2


@pytest.mark.parametrize("mutation", ["column", "trigger", "unknown_state", "missing_decision", "naive_time"])
def test_unknown_schema_or_state_refuses_without_cleanup(approvals, mutation):
    owner, create, _ = approvals
    create()
    with sqlite3.connect(owner.path) as db:
        db.executescript({
            "column": "ALTER TABLE pending ADD COLUMN unknown_reference TEXT",
            "trigger": "CREATE TRIGGER destructive AFTER DELETE ON pending BEGIN DELETE FROM pending; END;",
            "unknown_state": "UPDATE pending SET status='unreviewed'",
            "missing_decision": "UPDATE pending SET decision_at=NULL",
            "naive_time": "UPDATE pending SET decision_at='2020-01-01T00:00:00'",
        }[mutation])
    before = owner.path.read_bytes()
    with pytest.raises(RetentionError):
        owner.scan()
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("local_id", ['["str", "absent"]', '["str",1]', '["bool",true]',
                                       '["int",9223372036854775808]', '{}', 'null'])
def test_absent_row_still_requires_canonical_typed_identity(approvals, local_id):
    owner, create, _ = approvals
    create()
    row, = owner.scan()
    with pytest.raises(RetentionError, match="retention_owner_invalid"):
        owner.version(replace(row.identity, local_id=local_id))
    assert len(owner.scan()) == 1


@pytest.mark.parametrize("field,value", [("owner", "different"), ("store", "file:///different"),
                                          ("node_type", "revision"), ("contract", "foreign")])
def test_full_identity_prevents_cross_owner_deletion(approvals, field, value):
    owner, create, _ = approvals
    create()
    row, = owner.scan()
    with pytest.raises(RetentionError, match="retention_owner_invalid"):
        owner.delete(replace(row.identity, **{field: value}), row.version)
    assert owner.version(row.identity) == row.version


def test_replaced_identical_database_has_different_native_version(approvals):
    owner, create, _ = approvals
    create()
    row, = owner.scan()
    saved = owner.path.with_suffix(".preserved")
    owner.path.rename(saved)
    owner.path.write_bytes(saved.read_bytes())
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        owner.delete(row.identity, row.version)
    assert len(owner.scan()) == 1


@pytest.mark.parametrize("sidecar", ["", "-wal", "-shm", "-journal"])
def test_links_refuse_even_for_sidecars(approvals, sidecar, tmp_path):
    owner, create, _ = approvals
    create()
    path = Path(str(owner.path) + sidecar)
    if path.exists():
        path.rename(tmp_path / "saved")
    try:
        path.symlink_to(tmp_path / "absent")
    except OSError:
        pytest.skip("creating a native symbolic link is unavailable")
    with pytest.raises(RetentionError, match="retention_owner_path_invalid"):
        owner.scan()


def test_lost_exclusion_before_commit_rolls_back_effect(approvals):
    owner, create, allowed = approvals
    create()
    row, = owner.scan()
    calls = []

    def excluded():
        calls.append(True)
        if len(calls) == 3:  # open, immediately before DELETE, before COMMIT
            raise RetentionError("retention_invalid", "lost exclusion")

    owner.require_exclusion = excluded
    with pytest.raises(RetentionError, match="lost exclusion"):
        owner.delete(row.identity, row.version)
    assert owner.version(row.identity) == row.version
    allowed[0] = False


@pytest.mark.parametrize("action", ["reject", "block", "approve"])
def test_proposal_decisions_remain_memory_not_old_cache(tmp_path, monkeypatch, action):
    path = tmp_path / "proposals.sqlite"
    monkeypatch.setattr(proposals_state, "DB_PATH", path)
    proposals_state.touch_or_insert("candidate", "executor", 0)
    proposals_state.mark_action("candidate", action)
    owner = _SQLiteOwner(name="proposals_state", path=path, schema=proposals_state.init_schema,
                         table="proposals_state", primary_key="sig_key", node_type="proposal",
                         state=_proposal_state, require_exclusion=lambda: None, owner=None)
    row, = owner.scan()
    assert owner.state(row.values).roots == (RootKind.OPEN_REVISION,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(row.identity, row.version)


@pytest.mark.parametrize("state", change_intents.ALL_STATES)
def test_intent_native_schema_keeps_all_reversible_states(tmp_path, monkeypatch, state):
    path = tmp_path / "intents.sqlite"
    monkeypatch.setattr(change_intents.C, "DB_CHANGE_INTENTS", path)
    monkeypatch.setattr(change_intents, "_iso_utc_now", lambda: "2020-02-01T00:00:00Z")
    change_intents.init_db()
    intent = change_intents.ChangeIntent.new(
        origin_family="user", origin_module="test", intent_kind="create_executor",
        intent_target="sample", intent_summary="sample", intent_body={}, discovered_at=OLD,
    )
    intent.state = state
    intent.updated_at = "2020-02-01T00:00:00Z"
    intent.rolled_back_at = "2020-01-02T00:00:00Z" if state == "rolled_back" else None
    change_intents.upsert_intent(intent)
    owner = _SQLiteOwner(name="change_intents", path=path, schema=change_intents._SCHEMA,
                         table="change_intents", primary_key="id", node_type="revision",
                         state=_intent_state, require_exclusion=lambda: None, owner=None)
    row, = owner.scan()
    result = owner.state(row.values)
    if state == "rolled_back":
        assert result.eligible_after == "2020-05-01T00:00:00Z"
        owner.delete(row.identity, row.version)
        assert owner.scan() == ()
    else:
        assert result.roots == (RootKind.OPEN_REVISION,)
        with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
            owner.delete(row.identity, row.version)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="native process termination requires fork")
@pytest.mark.parametrize("point", ["before_effect", "after_effect", "after_outcome"])
def test_native_approval_recovers_original_receipt_after_process_death(approvals, tmp_path, point):
    owner, create, _ = approvals
    create()
    row, = owner.scan()
    state = owner.state(row.values)
    obj = OwnerObject(row.identity, row.version, state.state, state.created_at, state.eligible_after)
    root = tmp_path / "maintenance"
    root.mkdir(mode=0o755)
    holds = root / "holds.json"
    holds.write_bytes(canonical({"schema_version": 1, "holds": []}))
    holds.chmod(0o600)
    key = Ed25519PrivateKey.generate()
    public = {"receipt": key.public_key()}
    owners = {owner.name: owner}

    def maintenance():
        return Maintenance(root, root_owned=False, require_exclusion=lambda: None)

    maintenance().begin((obj,), run_id=RUN, observed_at=NOW, key_id="receipt",
                        private_key=key, public_keys=public, owners=owners)
    marker = hashlib.sha256((root / "active.json").read_bytes()).hexdigest()
    pid = os.fork()
    if pid == 0:
        def crash(stage):
            if stage == point:
                os.kill(os.getpid(), signal.SIGKILL)
        try:
            maintenance().resume(owners, public_keys=public, max_objects=10,
                                 max_seconds=20, crash=crash)
        finally:
            os._exit(91)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert hashlib.sha256((root / "active.json").read_bytes()).hexdigest() == marker
    result = maintenance().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert result["remaining"] == 0
    assert maintenance().finish(owners, public_keys=public, verify_recovery=lambda: None) == {
        "run_id": RUN, "deleted": 1, "preserved": 0,
    }
    assert owner.scan() == ()


def test_composition_reads_explicit_native_stores_despite_ambient_defaults(tmp_path, monkeypatch):
    from install.birth_retention_sqlite import _operational_sqlite_owners

    proposals = tmp_path / 'selected-proposals.sqlite'
    intents = tmp_path / 'selected-intents.sqlite'
    approvals = tmp_path / 'selected-approvals.sqlite'
    monkeypatch.setattr(proposals_state, 'DB_PATH', proposals)
    proposals_state.touch_or_insert('selected-proposal', 'executor', 0)
    monkeypatch.setattr(change_intents.C, 'DB_CHANGE_INTENTS', intents)
    change_intents.init_db()
    intent = change_intents.ChangeIntent.new(
        origin_family='user', origin_module='test', intent_kind='create_executor',
        intent_target='sample', intent_summary='sample', intent_body={}, discovered_at=OLD,
    )
    change_intents.upsert_intent(intent)
    approval = approval_registry.create_pending(
        channel='test', sender_id='operator', capability_class='read',
        action_verb='inspect', target_summary='selected', db_path=approvals)
    for path in (proposals, intents, approvals):
        path.chmod(0o600)

    foreign = tmp_path / 'foreign'
    monkeypatch.setattr(proposals_state, 'DB_PATH', foreign / 'proposals.sqlite')
    monkeypatch.setattr(change_intents.C, 'DB_CHANGE_INTENTS', foreign / 'intents.sqlite')
    monkeypatch.setattr(approval_registry, 'DEFAULT_DB_PATH', foreign / 'approvals.sqlite')
    monkeypatch.setenv('METNOS_APPROVALS_DB', str(foreign / 'override.sqlite'))
    monkeypatch.setenv('METNOS_PROPOSALS_STATE_DB', str(foreign / 'override-proposals.sqlite'))
    owners = _operational_sqlite_owners(
        lambda: None, None, proposals_state_path=proposals,
        change_intents_path=intents, approval_registry_path=approvals)
    observed = {owner.name: owner.scan() for owner in owners}
    assert [row.values['sig_key'] for row in observed['proposals_state']] == ['selected-proposal']
    assert [row.values['id'] for row in observed['change_intents']] == [intent.id]
    assert [row.values['token'] for row in observed['approval_registry']] == [approval.token]
    assert not foreign.exists()


def test_composition_has_no_implicit_path_fallback(tmp_path):
    from install.birth_retention_sqlite import _operational_sqlite_owners

    with pytest.raises(TypeError):
        _operational_sqlite_owners(lambda: None, None)
    absent = tmp_path / 'absent.sqlite'
    owners = _operational_sqlite_owners(
        lambda: None, None, proposals_state_path=absent,
        change_intents_path=absent, approval_registry_path=absent)
    for owner in owners:
        assert owner.scan() == ()  # Native absent-store inventory, without creation.
    assert not absent.exists()
