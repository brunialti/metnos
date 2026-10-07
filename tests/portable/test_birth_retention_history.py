"""Native backup/undo reference behavior and signed physical collection."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

import pytest

from executor_helpers import backup_file_for_undo, restore_file_from_undo_backup
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_history import _HistoryBackupOwner
from install.birth_retention_maintenance import plan
from install.birth_retention_sqlite import _utc
from test_birth_retention_artifacts import RUN, collection
from test_birth_retention_undo import OLD, native


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX history backups")
FUTURE = "2035-01-01T00:00:00Z"
PAYLOAD = b"original native backup bytes\n" * 8192


@pytest.fixture
def history(native, tmp_path, monkeypatch):
    root = native.path.parent / "_history"
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(root))
    owner = _HistoryBackupOwner(root=root, journal=native.owner)
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2035, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("install.birth_retention_history.datetime", Future)
    monkeypatch.setattr("test_birth_retention_artifacts.OBSERVED", FUTURE)
    source = tmp_path / "original.txt"
    def create(turn="first", payload=PAYLOAD, mode=0o644):
        source.write_bytes(payload)
        source.chmod(mode)
        os.utime(source, (OLD, OLD))
        monkeypatch.setenv("METNOS_TURN_ID", turn)
        old_umask = os.umask(0o077)
        try:
            return Path(backup_file_for_undo(source))
        finally:
            os.umask(old_umask)
    def attach(path, op="first", *, undone=False, turn=None, legacy=False):
        native.clock.value += 1
        native.log.append_pending(op, turn or op, "any_native_executor", {}, {}, actor="host")
        reference = {"blob_sha256": "sha256:" + path.stem} if legacy else {"blob_path": str(path)}
        native.log.append_done(op, {"results": [reference]})
        if undone:
            native.log.append_undone(op, {"ok_count": 1, "fail_count": 0})
        return native.owner.identity(op)
    return SimpleNamespace(root=root, owner=owner, undo=native, source=source,
                           create=create, attach=attach)


def files(history):
    return {item.identity.local_id: item for item in history.owner.inventory()
            if item.identity.owner == history.owner.name}


def candidates(history, tmp_path, holds=frozenset()):
    owners = {history.owner.name: history.owner, history.undo.owner.name: history.undo.owner}
    result = plan(history.owner.inventory(), observed_owners=frozenset(owners),
                  required_owners=frozenset(owners), observed_roots=frozenset(RootKind), holds=holds,
                  graph_path=tmp_path / "graph.sqlite", run_id=RUN, observed_at=FUTURE)
    return result, owners


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644, 0o444, 0o755])
def test_native_copy_permissions_and_fresh_backup_window(history, mode):
    path = history.create(mode=mode)
    obj = next(iter(files(history).values()))
    assert path.stat().st_mode & 0o777 == mode
    assert path.stat().st_mtime == OLD
    assert _utc(obj.eligible_after) >= datetime.now(timezone.utc) + timedelta(days=89)
    # The real backup restores its original bytes and copied permissions.
    history.source.unlink()
    history.source.write_bytes(b"changed")
    assert restore_file_from_undo_backup(history.source, str(path))
    assert history.source.read_bytes() == PAYLOAD
    assert history.source.stat().st_mode & 0o777 == mode


def test_native_undo_then_unreferenced_backup_signed_collection(history, tmp_path):
    path = history.create()
    other = history.create("other", b"other content")
    operation = history.attach(path, undone=True)
    history.attach(other, "other")
    history.undo.add("later-boundary")
    first, owners = candidates(history, tmp_path)
    assert {obj.identity for obj in first} == {operation}
    assert all(obj.state is NodeState.OPEN for obj in files(history).values())
    root, public, factory = collection(tmp_path, first, owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["completed"] == 1
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    second_dir = tmp_path / "next"
    second_dir.mkdir()
    second, owners = candidates(history, second_dir)
    assert [obj.identity.local_id for obj in second] == [str(path.relative_to(history.root))]
    assert path.stat().st_blocks * 512 > 0
    root, public, factory = collection(second_dir, second, owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["completed"] == 1
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not path.exists() and path.parent.is_dir()
    assert other.read_bytes() == b"other content"
    assert all(PAYLOAD not in p.read_bytes() for p in root.iterdir() if p.is_file())


def test_legacy_digest_keeps_every_native_copy(history, tmp_path):
    first, second = history.create(), history.create("second")
    operation = history.attach(first, legacy=True)
    objects = history.owner.inventory()
    linked = next(obj for obj in objects if obj.identity == operation).references
    assert {obj.identity for obj in files(history).values()} == set(linked)
    assert len(linked) == 2 and candidates(history, tmp_path)[0] == ()


def test_native_digest_fallback_when_original_path_is_absent(history):
    first, second = history.create(), history.create("second")
    history.undo.log.append_pending("legacy", "first", "compute", {}, {})
    history.undo.log.append_done("legacy", {"results": [{
        "blob_path": str(first), "blob_sha256": first.stem}]})
    first.unlink()
    assert next(iter(files(history).values())).state is NodeState.OPEN
    from reverse_patterns import _restore_blob_backup
    destination = history.source.parent / "restored"
    result = _restore_blob_backup({}, {"results": [{
        "path": str(destination), "blob_path": str(first), "blob_sha256": first.stem}]})
    assert result["ok_count"] == 1 and destination.read_bytes() == second.read_bytes()


def test_pending_crash_keeps_turn_and_unscoped_backups(history):
    history.create("crashed")
    history.create("no_turn", b"unscoped")
    history.create("unrelated", b"unrelated")
    history.undo.add("crashed", "crashed")
    observed = files(history)
    assert {key.split("/")[0] for key, obj in observed.items() if obj.state is NodeState.OPEN} == {
        "crashed", "no_turn"}


def test_new_receipt_after_plan_blocks_collection(history):
    path = history.create()
    obj = next(iter(files(history).values()))
    history.attach(path)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        history.owner.delete(obj.identity, obj.version)
    assert path.read_bytes() == PAYLOAD


def test_hold_is_physical_and_does_not_capture_another_copy(history, tmp_path):
    history.create()
    history.create("second")
    observed = list(files(history).values())
    selected, _ = candidates(history, tmp_path, frozenset({observed[0].identity.key.node_id}))
    assert {obj.identity for obj in selected} == {observed[1].identity}


@pytest.mark.parametrize("name", ["plan.json", "receipt.json", "quarantine", ".incomplete"])
def test_unreconciled_native_artifacts_block_whole_inventory(history, name):
    path = history.create()
    (path.parent / name).write_bytes(b"preserve until native closure is joined")
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        history.owner.inventory()
    assert path.exists()


@pytest.mark.parametrize("mutation", ["digest", "mode", "symlink", "hardlink", "fifo"])
def test_unsafe_or_corrupt_backups_are_never_candidates(history, mutation):
    path = history.create()
    if mutation == "digest":
        path.write_bytes(b"changed")
    elif mutation == "mode":
        path.chmod(0o666)
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(history.source)
    elif mutation == "hardlink":
        os.link(path, history.source.parent / "second-link")
    else:
        path.unlink()
        os.mkfifo(path, 0o600)
    with pytest.raises(RetentionError):
        history.owner.inventory()


def test_missing_receipt_backup_and_missing_journal_are_not_orphans(history):
    path = history.create()
    history.attach(path)
    path.unlink()
    with pytest.raises(RetentionError, match="referenced history backup absent"):
        history.owner.inventory()
    history.create()
    history.undo.path.unlink()
    with pytest.raises(RetentionError, match="history undo journal absent"):
        history.owner.inventory()


def test_replaced_version_and_foreign_store_are_refused(history):
    path = history.create()
    obj = next(iter(files(history).values()))
    path.unlink()
    history.create()
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        history.owner.delete(obj.identity, obj.version)
    with pytest.raises(RetentionError, match="foreign history identity"):
        history.owner.version(replace(obj.identity, store="file:///different"))


def test_changed_reference_snapshot_is_refused(history, monkeypatch):
    history.create()
    original, calls = history.owner._files, []
    def changed():
        result = original()
        calls.append(1)
        if len(calls) == 1:
            history.undo.add("crashed", "crashed", turn="first")
        return result
    monkeypatch.setattr(history.owner, "_files", changed)
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        history.owner.inventory()


@pytest.mark.skipif(sys.platform != "linux", reason="real process death")
def test_sigkill_after_unlink_resumes_original_signed_intent(history, tmp_path, monkeypatch):
    path = history.create()
    selected, owners = candidates(history, tmp_path)
    root, public, factory = collection(tmp_path, selected, owners)
    before = sorted((p.name, p.read_bytes()) for p in root.glob("*.json"))
    pid = os.fork()
    if pid == 0:
        real_unlink = os.unlink
        def killed(name, *args, **kwargs):
            real_unlink(name, *args, **kwargs)
            if name == path.name:
                os.kill(os.getpid(), signal.SIGKILL)
        os.unlink = killed
        factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
        os._exit(91)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert not path.exists() and (root / "active.json").exists()
    assert before == sorted((p.name, p.read_bytes()) for p in root.glob("*.json"))
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["completed"] == 1
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not (root / "active.json").exists()
