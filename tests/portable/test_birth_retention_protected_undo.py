"""Actual native encryption/journal and original-receipt recovery under F6."""
from dataclasses import replace
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
import signal
import sqlite3
import sys
from types import SimpleNamespace

import pytest

import protected_undo
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import plan
from install.birth_retention_protected_undo import _ProtectedUndoOwner
from test_birth_retention_artifacts import OBSERVED, RUN, collection
from test_birth_retention_undo import OLD, native


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX protected files")
SECRET = b"session-secret-never-in-a-receipt\n" * 2048


@pytest.fixture
def secured(native, monkeypatch):
    config = native.path.parent / "config"
    config.mkdir(mode=0o750)
    key = config / "admin.key"
    key.write_text("b" * 64)
    key.chmod(0o600)
    root = native.path.parent / "undo_blobs"
    monkeypatch.setattr(protected_undo, "ADMIN_KEY_PATH", key)
    monkeypatch.setattr(protected_undo, "BLOB_DIR", root)
    monkeypatch.setattr(protected_undo, "time", SimpleNamespace(time=lambda: native.clock.value))
    monkeypatch.setenv("METNOS_UNDO_RETENTION_DAYS", "30")
    owner = _ProtectedUndoOwner(root=root, journal=native.owner, key_path=key)
    def create(actor="host", namespace="test.cookie"):
        handle = protected_undo.store(SECRET, owner=actor, namespace=namespace)
        path = root / (handle + ".age")
        os.utime(path, (OLD, OLD))
        return handle, path
    def attach(handle, op="first", *, actor="host", recovered=False, partial=False):
        native.clock.value += 1
        native.log.append_pending(op, op, "compute", {}, {}, actor=actor)
        native.log.append_done(op, {"_undo": {"outcome": "reversible", "cookie_state": {
            "before_present": True, "before_blob": handle,
            "before_sha256": hashlib.sha256(SECRET).hexdigest()}}})
        if recovered or partial:
            native.log.append_undone(op, {"ok_count": 1, "fail_count": int(partial)})
        return native.owner.identity(op)
    return SimpleNamespace(undo=native, key=key, root=root, owner=owner, create=create, attach=attach)


def files(secured):
    return {obj.identity.local_id: obj for obj in secured.owner.inventory()
            if obj.identity.owner == secured.owner.name}


def test_native_receipts_then_secrets_are_collected_preserving_other_actor(secured, tmp_path):
    first, first_path = secured.create()
    other, other_path = secured.create("guest:other")
    undo_id = secured.attach(first, recovered=True)
    secured.attach(other, "second", actor="guest:other")
    secured.undo.add("later-no-effect")
    owner = secured.owner
    owners = {owner.name: owner, secured.undo.owner.name: secured.undo.owner}
    observed = owner.inventory()
    assert all(obj.state is NodeState.OPEN for obj in observed if obj.identity.owner == owner.name)
    assert owner.identity(first) in next(obj for obj in observed if obj.identity == undo_id).references
    # The fixture's universe only attests these two native stores.
    candidates = plan(observed, observed_owners=frozenset(owners), required_owners=frozenset(owners),
                      observed_roots=frozenset(RootKind), holds=frozenset(),
                      graph_path=tmp_path / "projection.sqlite", run_id=RUN, observed_at=OBSERVED)
    assert {obj.identity for obj in candidates} == {undo_id}
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    _, public, factory = collection(first_dir, candidates, owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["completed"] == 1
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert first_path.exists() and other_path.exists()
    candidate = files(secured)[first]
    assert candidate.state is NodeState.CLOSED and files(secured)[other].state is NodeState.OPEN
    allocated = first_path.stat().st_blocks * 512
    assert allocated > 0
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    root, public, factory = collection(second_dir, (candidate,), owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["completed"] == 1
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not first_path.exists()
    assert protected_undo.load(other, owner="guest:other", namespace="test.cookie") == SECRET
    assert not (root / "active.json").exists()
    assert all(SECRET not in path.read_bytes() for path in root.iterdir() if path.is_file())
    assert SECRET not in secured.undo.path.read_bytes() and SECRET not in other_path.read_bytes()


def test_any_nested_native_handle_is_retained_without_executor_specific_names(secured):
    handle, path = secured.create(namespace="another.application")
    secured.undo.log.append_pending("arbitrary", "turn", "different_executor", {}, {}, actor="host")
    secured.undo.log.append_done("arbitrary", {"future_receipt": ["x", {"opaque": handle}]})
    obj = files(secured)[handle]
    assert obj.state is NodeState.OPEN
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        secured.owner.delete(obj.identity, obj.version)
    assert protected_undo.load(handle, owner="host", namespace="another.application") == SECRET
    with pytest.raises(PermissionError):
        protected_undo.load(handle, owner="host", namespace="test.cookie")
    assert path.exists()


@pytest.mark.parametrize("condition", ["crashed", "partial", "invalid"])
def test_unrecorded_secret_is_kept_for_uncertain_operation_of_its_actor(secured, condition):
    handle, _ = secured.create()
    other, _ = secured.create("guest:other")
    secured.undo.add("uncertain", condition)
    observed = files(secured)
    assert observed[handle].roots == (RootKind.IN_PROGRESS_JOB,)
    assert observed[other].state is NodeState.CLOSED


@pytest.mark.parametrize("recovered,partial", [(False, False), (True, False), (False, True)])
def test_missing_native_blob_is_allowed_only_after_complete_undo(secured, recovered, partial):
    handle, path = secured.create()
    secured.attach(handle, recovered=recovered, partial=partial)
    # The actual native reverse operation discards its authenticated blob.
    assert protected_undo.discard(handle, owner="host", namespace="test.cookie")
    assert not path.exists()
    if recovered:
        assert not files(secured)
    else:
        with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
            secured.owner.inventory()


def test_absent_journal_is_not_proof_that_secret_is_unreferenced(secured):
    _, path = secured.create()
    secured.undo.path.unlink()
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        secured.owner.inventory()
    assert path.exists()


@pytest.mark.parametrize("kind", ["actor", "receipt_digest"])
def test_authenticated_native_file_must_match_its_receipt_binding(secured, kind):
    handle, _ = secured.create("guest:other" if kind == "actor" else "host")
    secured.attach(handle)
    if kind == "receipt_digest":
        payload = secured.undo.path.read_text().replace(hashlib.sha256(SECRET).hexdigest(), "c" * 64)
        secured.undo.path.write_text(payload)
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        secured.owner.inventory()


@pytest.mark.parametrize("damage", ["blob_mode", "key_mode", "root_mode", "hardlink", "symlink", "fifo",
                                  "wrong_key", "empty_key", "truncated", "unknown_entry", "key_symlink"])
def test_unsafe_custody_or_ciphertext_blocks_collection(secured, damage, tmp_path):
    handle, path = secured.create()
    if damage == "blob_mode":
        path.chmod(0o640)
    elif damage == "key_mode":
        secured.key.chmod(0o640)
    elif damage == "root_mode":
        secured.root.chmod(0o750)
    elif damage == "hardlink":
        os.link(path, tmp_path / "alias")
    elif damage == "symlink":
        target = tmp_path / "original"
        path.rename(target)
        path.symlink_to(target)
    elif damage == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif damage == "wrong_key":
        secured.key.write_text("c" * 64)
    elif damage == "empty_key":
        secured.key.write_text("")
    elif damage == "truncated":
        path.write_bytes(path.read_bytes()[:-10])
    elif damage == "unknown_entry":
        (secured.root / "unexpected").write_text("preserve")
    elif damage == "key_symlink":
        target = tmp_path / "original-key"
        secured.key.rename(target)
        secured.key.symlink_to(target)
    with pytest.raises(RetentionError) as error:
        secured.owner.inventory()
    assert "session-secret" not in str(error.value)
    assert path.exists()


@pytest.mark.parametrize("field,value", [("format", 2), ("format", True), ("created_at", True),
                                        ("expires_at", 0), ("sha256", "a" * 64),
                                        ("namespace", "bad namespace"), ("owner", ""), ("extra", 1)])
def test_authentication_does_not_accept_invalid_envelope_semantics(secured, field, value):
    _, path = secured.create()
    salt_text, token = path.read_bytes().split(b"\n", 1)
    fernet = protected_undo._fernet(base64.urlsafe_b64decode(salt_text))
    envelope = json.loads(fernet.decrypt(token))
    envelope[field] = value
    path.write_bytes(salt_text + b"\n" + fernet.encrypt(json.dumps(envelope).encode()))
    with pytest.raises(RetentionError, match="retention_owner_invalid"):
        secured.owner.inventory()
    assert path.exists()


@pytest.mark.parametrize("change", ["new_reference", "file", "key", "foreign", "recent", "unexpired"])
def test_effect_rechecks_reference_content_custody_identity_and_both_windows(secured, monkeypatch, change):
    if change in {"recent", "unexpired"}:
        secured.undo.clock.value = datetime.now(timezone.utc).timestamp() - (100 * 86400 if change == "unexpired" else 0)
        if change == "unexpired":
            monkeypatch.setenv("METNOS_UNDO_RETENTION_DAYS", "365")
    handle, path = secured.create()
    obj = files(secured)[handle]
    identity, version = obj.identity, obj.version
    if change == "new_reference":
        secured.attach(handle)
    elif change == "file":
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data)
        path.chmod(0o600)
        os.utime(path, (OLD, OLD))
    elif change == "key":
        data = secured.key.read_bytes()
        secured.key.unlink()
        secured.key.write_bytes(data)
        secured.key.chmod(0o600)
    elif change == "foreign":
        identity = replace(identity, store=identity.store + "-other")
    with pytest.raises(RetentionError):
        secured.owner.delete(identity, version)
    assert path.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native SIGKILL and directory fsync")
@pytest.mark.parametrize("point", ["before_effect", "after_unlink", "after_effect", "after_outcome"])
def test_killed_secret_collection_resumes_original_authenticated_receipt(secured, tmp_path, monkeypatch, point):
    handle, path = secured.create()
    candidate = files(secured)[handle]
    owners = {secured.owner.name: secured.owner}
    root, public, factory = collection(tmp_path, (candidate,), owners)
    journal = root / (RUN[7:] + ".sqlite")
    with sqlite3.connect(journal) as db:
        original = db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall()
    parent_identity = (path.parent.stat().st_dev, path.parent.stat().st_ino)
    fsync = os.fsync
    child = os.fork()
    if child == 0:
        try:
            if point == "after_unlink":
                def interrupt_sync(fd):
                    info = os.fstat(fd)
                    if (info.st_dev, info.st_ino) == parent_identity and not path.exists():
                        os.kill(os.getpid(), signal.SIGKILL)
                    return fsync(fd)
                os.fsync = interrupt_sync
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20,
                             crash=lambda stage: os.kill(os.getpid(), signal.SIGKILL) if stage == point else None)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert path.exists() == (point == "before_effect")
    assert (root / "active.json").exists()
    synchronized = []
    def observe_sync(fd):
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) == parent_identity:
            synchronized.append(True)
        return fsync(fd)
    monkeypatch.setattr(os, "fsync", observe_sync)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["completed"] >= 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not path.exists() and synchronized and not (root / "active.json").exists()
    with sqlite3.connect(journal) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == original
        assert db.execute("SELECT status FROM intents").fetchall() == [("deleted",)]
    assert secured.owner.version(candidate.identity) is None
    assert SECRET not in journal.read_bytes()
