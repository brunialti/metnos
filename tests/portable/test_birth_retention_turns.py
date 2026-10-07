"""Native TurnLog serialization and durable recovery of its daily journals."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
import signal
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from config import append_private_bytes
from executor_birth_feedback import dispatch_identifier_reference, make_execution_receipt
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_jsonl import _TEMP_PREFIX
from install.birth_retention_turns import _TurnLogOwner, _receipts
from manifest_inventory import ContractId, ManifestOrigin
from test_birth_retention_artifacts import RUN, collection


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX turn journals")
OLD = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
DAY = "2020-01-01.jsonl"
CID = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
DIGEST = "sha256:" + "1" * 64


def receipt(turn_id, tool="demo"):
    return make_execution_receipt(
        request_id=DIGEST, turn_id=dispatch_identifier_reference("turn", turn_id),
        reduced_query_ref=DIGEST, arguments={"text": "è🙂"}, reduced_output={"ok": True},
        contract_id=CID, executor_name=tool, candidate_id=DIGEST,
        generation_id="sha256:" + "2" * 64, dispatched_at="2020-01-01T00:00:00Z",
        completed_at="2020-01-01T00:00:01Z")


def record(turn_id, *, actor="host", **changes):
    # The native runtime imports POSIX-only owners. Load it only when these
    # POSIX journal tests actually run, after pytest applies the platform mark.
    from agent_runtime import StepLog, TurnLog

    # Same native dataclasses and serialization used by TurnLog.write. These
    # tests exercise storage/feedback, without invoking its routing side effects.
    value = asdict(TurnLog(ts_start=OLD, ts_end=OLD + 1, turn_id=turn_id,
                          user_query="test è🙂", actor=actor, owner_user_id=actor,
                          final_kind="answer", outcome="completed", steps=[
                              StepLog(step_num=1, chosen_tool="demo",
                                      execution_receipt=receipt(turn_id))]))
    value.update(changes)
    return value


@pytest.fixture
def native(tmp_path, monkeypatch):
    import turn_feedback

    root = tmp_path / "turns"
    root.mkdir(mode=0o700)
    owner = _TurnLogOwner(root=root, owner=None, require_exclusion=lambda: None)
    monkeypatch.setattr(turn_feedback, "TURNS_DIR", root)

    def append(value, *, day=DAY):
        raw = (json.dumps(value, ensure_ascii=False) + "\n").encode()
        append_private_bytes(root / day, raw)
        return raw

    return SimpleNamespace(root=root, path=root / DAY, owner=owner, append=append,
                           load=turn_feedback._load_turn)


def objects(native):
    return {entry.object.identity.local_id: entry.object for entry in native.owner.scan()}


def test_native_feedback_and_another_owner_survive_compaction_byte_for_byte(native, tmp_path):
    native.append(record("old"))
    append_private_bytes(native.path, b" \n")
    remaining = b" \n" + native.append(record("other", actor="another"))
    # Preserve even native whitespace lines, without canonicalizing survivors.
    remaining += native.append(record("waiting", outcome="awaiting_input", final_kind="ask"))
    before = objects(native)
    assert before["old"].state is NodeState.CLOSED
    assert before["waiting"].roots == (RootKind.OPEN_APPROVAL,)
    owners = {native.owner.name: native.owner}
    root, public, factory = collection(tmp_path, (before["old"],), owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert native.path.read_bytes() == remaining
    after = objects(native)
    assert after == {key: value for key, value in before.items() if key != "old"}
    assert native.load("old") is None
    assert _receipts(native.load("other")) == ((receipt("other"),), False)
    assert not (root / "active.json").exists()


@pytest.mark.parametrize("condition", ["open", "pending", "ask", "old_format", "reversed_time",
                                      "missing_receipt", "tampered", "wrong_turn", "wrong_tool",
                                      "foreign_actor", "earlier_open"])
def test_incomplete_or_ambiguous_native_evidence_is_retained(native, condition):
    value = record("kept")
    if condition == "open":
        value["ts_end"] = 0
    elif condition == "pending":
        value["pending_location"] = {"dialog_id": "waiting"}
    elif condition == "ask":
        value["final_kind"] = "needs_inputs"
    elif condition == "old_format":
        del value["outcome"]
    elif condition == "reversed_time":
        value["ts_end"] = OLD - 1
    elif condition == "missing_receipt":
        del value["steps"][0]["execution_receipt"]
    elif condition == "tampered":
        value["steps"][0]["execution_receipt"]["arguments"] = {"text": "different"}
    elif condition == "wrong_turn":
        value["steps"][0]["execution_receipt"] = asdict(receipt("unrelated"))
    elif condition == "wrong_tool":
        value["steps"][0]["chosen_tool"] = "some_other_tool"
    elif condition == "foreign_actor":
        native.append(record("kept", actor="another"))
    elif condition == "earlier_open":
        native.append(record("kept", ts_end=0))
    native.append(value)
    obj = objects(native)["kept"]
    assert obj.state is NodeState.OPEN and obj.roots
    before = native.path.read_bytes()
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.owner.delete(obj.identity, obj.version)
    assert native.path.read_bytes() == before


def test_protocol_final_answer_needs_no_executor_receipt(native):
    from agent_runtime import StepLog

    native.append(record("final", steps=[asdict(StepLog(step_num=1, chosen_tool="final_answer"))]))
    obj = objects(native)["final"]
    assert obj.state is NodeState.CLOSED
    native.owner.delete(obj.identity, obj.version)
    assert native.owner.version(obj.identity) is None


def test_same_turn_id_in_distinct_daily_files_keeps_distinct_physical_identity(native):
    native.append(record("duplicate"))
    native.append(record("duplicate"), day="2020-01-02.jsonl")
    first, second = (entry.object for entry in native.owner.scan())
    assert first.identity != second.identity
    native.owner.delete(first.identity, first.version)
    assert native.owner.version(second.identity) == second.version


@pytest.mark.parametrize("change", ["new_pending", "foreign_identity", "recent"])
def test_deletion_rechecks_native_version_identity_and_retention_window(native, change):
    value = record("old")
    if change == "recent":
        value.update(ts_start=datetime.now(timezone.utc).timestamp(),
                     ts_end=datetime.now(timezone.utc).timestamp())
    native.append(value)
    obj = objects(native)["old"]
    identity = obj.identity
    if change == "new_pending":
        native.append(record("old", ts_end=0))
    elif change == "foreign_identity":
        identity = replace(identity, store=(native.root.parent / DAY).as_uri())
    before = native.path.read_bytes()
    with pytest.raises(RetentionError):
        native.owner.delete(identity, obj.version)
    assert native.path.read_bytes() == before


@pytest.mark.parametrize("damage", ["truncated", "duplicate_key", "nan", "wrong_type",
                                   "symlink", "hardlink", "mode", "fifo", "archive"])
def test_inventory_refuses_malformed_or_unsafe_storage(native, tmp_path, damage):
    native.append(record("old"))
    if damage == "truncated":
        native.path.write_bytes(native.path.read_bytes()[:-1])
    elif damage == "duplicate_key":
        native.path.write_bytes(b'{"turn_id":"x", "turn_id":"y"}\n')
    elif damage == "nan":
        native.path.write_bytes(native.path.read_bytes().replace(b'"ts_start": 1577836800.0', b'"ts_start": NaN'))
    elif damage == "wrong_type":
        native.path.write_bytes(b'{"turn_id":"x", "steps": 1, "ts_start": 1}\n')
    elif damage == "symlink":
        target = tmp_path / "elsewhere"
        native.path.rename(target)
        native.path.symlink_to(target)
    elif damage == "hardlink":
        os.link(native.path, tmp_path / "alias")
    elif damage == "mode":
        native.path.chmod(0o666)
    elif damage == "fifo":
        native.path.unlink()
        os.mkfifo(native.path, 0o600)
    elif damage == "archive":
        (native.root / "archive").mkdir(mode=0o700)
    with pytest.raises(RetentionError):
        native.owner.scan()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SIGKILL and native directory fsync")
@pytest.mark.parametrize("point", ["before_effect", "partial_copy", "before_replace",
                                  "after_replace", "after_effect", "after_outcome"])
def test_interruption_recovers_original_receipt_and_native_feedback(native, tmp_path, monkeypatch, point):
    native.append(record("old"))
    remaining = native.append(record("other", actor="another"))
    obj = objects(native)["old"]
    owners = {native.owner.name: native.owner}
    root, public, factory = collection(tmp_path, (obj,), owners)
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
        with pytest.raises(RetentionError):
            native.owner.scan()
    synchronized = []
    fsync = os.fsync
    def sync(fd):
        if os.fstat(fd).st_ino == native.root.stat().st_ino:
            synchronized.append(True)
        return fsync(fd)
    monkeypatch.setattr(os, "fsync", sync)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert synchronized and native.path.read_bytes() == remaining
    assert _receipts(native.load("other")) == ((receipt("other"),), False)
    assert not tuple(native.root.glob(_TEMP_PREFIX + "*"))
    with sqlite3.connect(journal) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == original
        assert db.execute("SELECT status FROM intents").fetchall() == [("deleted",)]
