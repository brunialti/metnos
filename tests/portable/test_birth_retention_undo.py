"""Native undo behavior and receipt recovery, scoped to the JSONL owner."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import signal
import sqlite3
import stat
import sys
from types import SimpleNamespace

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_undo import _TEMP_PREFIX, _UndoOwner
from test_birth_retention_artifacts import RUN, collection


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX undo journal")
OLD = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def native(tmp_path, monkeypatch):
    import undo

    clock = SimpleNamespace(value=OLD)
    monkeypatch.setattr(undo, "time", SimpleNamespace(time=lambda: clock.value))
    path = tmp_path / "data" / "undo.jsonl"
    # Installed service units use UMask=0077. Do not inherit the developer
    # shell's group-writable default while exercising the native writer.
    previous_umask = os.umask(0o077)
    try:
        log = undo.UndoLog(path)
    finally:
        os.umask(previous_umask)
    owner = _UndoOwner(path=path, require_exclusion=lambda: None, owner=None)

    def add(op_id, kind="no_effect", *, actor="host", turn=None):
        clock.value += 1
        log.append_pending(op_id, turn or op_id, "compute", {}, {}, actor=actor)
        if kind in {"no_effect", "irreversible"}:
            log.append_closed(op_id, kind)
        elif kind == "invalid":
            log.append_completion(op_id, {}, outcome_contract="per_execution")
        elif kind in {"reversible", "undone", "partial"}:
            log.append_done(op_id, {"ok_count": 1, "results": [{"value": "è🙂"}]})
            if kind != "reversible":
                log.append_undone(op_id, {"ok_count": 1, "fail_count": int(kind == "partial")})
        else:
            assert kind == "crashed"
        return owner.identity(op_id)

    return SimpleNamespace(path=path, log=log, owner=owner, add=add, clock=clock)


def objects(native):
    return {item.object.identity.local_id: item.object for item in native.owner.scan()}


def test_native_crashes_reversals_partial_failures_and_actor_boundaries_are_retained(native):
    for kind in ("no_effect", "irreversible", "undone", "reversible", "partial", "crashed", "invalid"):
        native.add(kind, kind)
    native.add("host-boundary")
    native.add("old-guest", actor="guest:a")
    native.add("guest-boundary", actor="guest:a")
    observed = objects(native)
    assert {key for key, obj in observed.items() if obj.state is NodeState.CLOSED} == {
        "no_effect", "irreversible", "undone", "old-guest"}
    assert observed["reversible"].roots == (RootKind.ADMITTED_ROLLBACKABLE,)
    assert observed["crashed"].roots == (RootKind.IN_PROGRESS_JOB,)
    assert all(observed[key].roots == (RootKind.OPEN_AUDIT,)
               for key in ("partial", "invalid", "host-boundary", "guest-boundary"))
    assert [row["op_id"] for row in native.log.find_crashed()] == ["crashed"]
    assert native.log.latest_turn_done(actor="host") == native.log.latest_turn_done(actor="guest:a") == []
    for key in ("reversible", "partial", "crashed", "invalid", "host-boundary", "guest-boundary"):
        obj = observed[key]
        with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
            native.owner.delete(obj.identity, obj.version)


@pytest.mark.parametrize("tied", [False, True])
def test_compaction_preserves_native_undo_boundary_and_other_operation_versions(native, tmp_path, tied):
    mode = 0o644 if tied else 0o600
    native.path.chmod(mode)
    native.add("still-reversible", "reversible")
    for name in ("expired-a", "expired-b", "boundary"):
        if tied:
            native.clock.value = OLD
        native.add(name)
    with native.path.open("ab") as stream:
        stream.write(b"\n  \n")
    before = native.path.read_bytes()
    observed = objects(native)
    candidates = tuple(observed[key] for key in ("expired-a", "expired-b"))
    owners = {native.owner.name: native.owner}
    root, public, factory = collection(tmp_path, candidates, owners)
    for remaining in (1, 0):
        result = factory().resume(owners, public_keys=public, max_objects=1, max_seconds=20)
        assert result["completed"] == 1 and result["remaining"] == remaining
        for key, obj in objects(native).items():
            assert obj.version == observed[key].version
        assert native.log.latest_turn_done(actor="host") == []
        assert native.log.latest_turn_done() == []
        assert [op["op_id"] for op in native.log.open_ops_for_executor("compute")] == ["still-reversible"]
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    expected = b"".join(line for line in before.splitlines(keepends=True)
                        if not line.strip() or json.loads(line)["op_id"] not in {"expired-a", "expired-b"})
    assert native.path.read_bytes() == expected and len(expected) < len(before)
    assert not (root / "active.json").exists()
    assert stat.S_IMODE(native.path.stat().st_mode) == mode


def test_ambiguous_native_history_is_audit_retained(native):
    native.add("duplicate")
    native.log.append_closed("duplicate", "irreversible")
    native.add("missing-done", "crashed")
    native.log.append_undone("missing-done", {"ok_count": 1})
    native.add("boundary")
    assert objects(native)["duplicate"].roots == objects(native)["missing-done"].roots == (RootKind.OPEN_AUDIT,)


@pytest.mark.parametrize("damage", ["truncated", "duplicate-key", "unknown", "symlink", "hardlink", "mode", "fifo"])
def test_invalid_journal_blocks_inventory_without_touching_other_data(native, tmp_path, damage):
    native.add("old")
    protected = tmp_path / "original"
    protected.write_bytes(b"must survive")
    if damage in {"truncated", "duplicate-key", "unknown"}:
        with native.path.open("ab") as stream:
            stream.write({"truncated": b'{"type":',
                          "duplicate-key": b'{"type":"done","type":"pending"}\n',
                          "unknown": b'{"type":"new","op_id":"old","ts":0}\n'}[damage])
    elif damage == "symlink":
        native.path.unlink()
        native.path.symlink_to(protected)
    elif damage == "hardlink":
        os.link(native.path, tmp_path / "alias")
    elif damage == "mode":
        native.path.chmod(0o666)
    else:
        native.path.unlink()
        os.mkfifo(native.path, 0o600)
    with pytest.raises(RetentionError):
        native.owner.scan()
    assert protected.read_bytes() == b"must survive"


@pytest.mark.parametrize("change", ["appended", "foreign", "recent", "parent-replaced"])
def test_compaction_rechecks_version_identity_window_and_parent(native, change):
    native.add("old")
    native.add("boundary")
    candidate = objects(native)["old"]
    identity = candidate.identity
    if change == "appended":
        native.log.append_closed("old", "irreversible")
    elif change == "foreign":
        identity = replace(identity, store="file:///another-journal")
    elif change == "recent":
        native.clock.value = datetime.now(timezone.utc).timestamp()
        native.add("recent")
        native.add("new-boundary")
        candidate = objects(native)["recent"]
        identity = candidate.identity
    else:
        native.path.parent.rename(native.path.parent.with_name("previous"))
        native.path.parent.mkdir()
        native.path.write_bytes((native.path.parent.with_name("previous") / native.path.name).read_bytes())
    before = native.path.read_bytes()
    with pytest.raises(RetentionError):
        native.owner.delete(identity, candidate.version)
    assert native.path.read_bytes() == before


def test_missing_optional_journal_is_observed_without_creating_it(tmp_path):
    owner = _UndoOwner(path=tmp_path / "absent" / "undo.jsonl", require_exclusion=lambda: None, owner=None)
    assert owner.scan() == () and not owner.path.parent.exists()
    owner.path.parent.mkdir()
    assert owner.scan() == () and not owner.path.exists()


def test_read_atime_does_not_invalidate_unchanged_journal(native):
    native.add("boundary")
    os.utime(native.path, (1, 2))
    assert objects(native)["boundary"].roots == (RootKind.OPEN_AUDIT,)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SIGKILL and native directory fsync")
@pytest.mark.parametrize("point", ["before_effect", "partial_copy", "before_replace", "after_replace", "after_effect", "after_outcome"])
def test_interrupted_native_compaction_resumes_original_signed_receipt(native, tmp_path, monkeypatch, point):
    native.add("old")
    native.add("boundary")
    candidate = objects(native)["old"]
    owners = {native.owner.name: native.owner}
    root, public, factory = collection(tmp_path, (candidate,), owners)
    journal = root / (RUN[7:] + ".sqlite")
    with sqlite3.connect(journal) as db:
        original = db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall()
    before = native.path.read_bytes()
    write, rename = os.write, os.replace
    child = os.fork()
    if child == 0:
        try:
            if point == "partial_copy":
                def partial(fd, data):
                    if _TEMP_PREFIX in os.readlink(f"/proc/self/fd/{fd}"):
                        write(fd, data[:max(1, len(data) // 2)])
                        os.kill(os.getpid(), signal.SIGKILL)
                    return write(fd, data)
                os.write = partial
            if point in {"before_replace", "after_replace"}:
                def interrupted(source, destination, **kwargs):
                    if str(source).startswith(_TEMP_PREFIX):
                        if point == "after_replace":
                            rename(source, destination, **kwargs)
                        os.kill(os.getpid(), signal.SIGKILL)
                    return rename(source, destination, **kwargs)
                os.replace = interrupted
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20,
                             crash=lambda stage: os.kill(os.getpid(), signal.SIGKILL) if stage == point else None)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert (root / "active.json").exists()
    if point in {"partial_copy", "before_replace"}:
        assert native.path.read_bytes() == before
        with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
            native.owner.scan()
    synchronized = []
    fsync = os.fsync
    def sync(fd):
        if os.fstat(fd).st_ino == native.path.parent.stat().st_ino:
            synchronized.append(True)
        return fsync(fd)
    monkeypatch.setattr(os, "fsync", sync)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    assert synchronized
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert set(objects(native)) == {"boundary"}
    assert native.log.latest_turn_done(actor="host") == []
    assert not tuple(native.path.parent.glob(_TEMP_PREFIX + "*"))
    with sqlite3.connect(journal) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == original
        assert db.execute("SELECT status FROM intents").fetchall() == [("deleted",)]


@pytest.mark.parametrize("damage", ["content", "hardlink", "mode"])
def test_recovery_rejects_unrelated_or_unsafe_compaction_copy(native, tmp_path, damage):
    native.add("old")
    native.add("boundary")
    candidate = objects(native)["old"]
    path = native.path.with_name(_TEMP_PREFIX + candidate.identity.key.node_id[7:] + ".tmp")
    path.write_bytes(b"not the preserved journal" if damage == "content" else b"")
    path.chmod(0o666 if damage == "mode" else 0o600)
    if damage == "hardlink":
        os.link(path, tmp_path / "alias")
    before = native.path.read_bytes()
    with pytest.raises(RetentionError):
        native.owner.delete(candidate.identity, candidate.version)
    assert native.path.read_bytes() == before
