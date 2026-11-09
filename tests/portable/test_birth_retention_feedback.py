"""Physical compaction must preserve native feedback decisions."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import signal
import sqlite3
import sys
from types import SimpleNamespace

import pytest

import config
import turn_feedback
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_feedback import _FeedbackOwner
from install.birth_retention_jsonl import _TEMP_PREFIX
from test_birth_retention_artifacts import RUN, collection
from test_birth_retention_turns import OLD


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX feedback owner")


def record(turn_id, **changes):
    value = dict(turn_id=turn_id, action="ok", by="user", ts=OLD,
                 canonical=None, user_query="query", effects=[], approved_pipeline=["demo"])
    value.update(changes)
    if value["action"] == "error":
        value["rejected_pipeline"] = value.pop("approved_pipeline")
    return value


@pytest.fixture
def native(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    (data / "birth").mkdir(mode=0o700)
    monkeypatch.setattr(config, "PATH_USER_STATE", data)
    monkeypatch.setattr(config, "PATH_USER_DATA", data)
    path = data / "turn_feedback.jsonl"
    monkeypatch.setattr(turn_feedback, "FEEDBACK_PATH", path)
    owner = _FeedbackOwner(path=path, require_exclusion=lambda: None, owner=None)

    def append(value):
        # Native service writers run with UMask=0077; the administrative
        # receipt provisioner in collection() has a different custody policy.
        previous_umask = os.umask(0o077)
        try:
            turn_feedback._append_feedback(value)
        finally:
            os.umask(previous_umask)
        return (json.dumps(value, ensure_ascii=False) + "\n").encode()

    def tail():
        raw = []
        for index in range(turn_feedback.FEEDBACK_LOOKBACK):
            if index == 50:
                with path.open("ab") as stream:
                    stream.write(b" \n")
                raw.append(b" \n")
            else:
                raw.append(append(record("recent-" + str(index), by="another-user",
                    action="error" if index > 197 else "ok", ts=OLD + index)))
        return b"".join(raw)

    return SimpleNamespace(path=path, owner=owner, append=append, tail=tail)


def objects(native):
    return {entry.object.identity.local_id: entry.object for entry in native.owner.scan()}


def decisions():
    from change_intent_adapters.user_feedback import iter_user_feedback
    from change_observer import _verify_reject_pattern
    intent = SimpleNamespace(intent_body={"canonical_query": "canonical"},
        intent_target="canonical", applied_at="2019-01-01T00:00:00Z")
    return (
        turn_feedback.count_consecutive_errors_for_query("query"),
        turn_feedback.count_consecutive_errors_for_tool("demo"),
        turn_feedback.rejected_pipelines_for_query("query"),
        tuple((item.intent_target, item.intent_body, item.score)
              for item in iter_user_feedback()),
        _verify_reject_pattern(intent),
    )


def test_collection_preserves_all_native_learning_consumers_and_other_user_bytes(native, tmp_path):
    native.append(record("closed"))
    remaining = native.append(record("reject-1", action="error", canonical="canonical"))
    native.append(record("closed", action="repeat", ts=OLD + 1))
    remaining += native.append(record("reject-2", action="error", canonical="canonical"))
    remaining += native.append(record("single-reject", action="error", canonical="future-pattern"))
    remaining += native.tail()
    before = objects(native)
    observed = decisions()
    assert observed[:3] == (2, 2, [["demo"]])
    assert observed[3][0][1]["n_rejections"] == 2
    assert observed[4][0] == "rollback"
    assert before["closed"].state is NodeState.CLOSED
    assert before["single-reject"].roots == (RootKind.OPEN_REVISION,)
    owners = {native.owner.name: native.owner}
    root, public, factory = collection(tmp_path, (before["closed"],), owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert native.path.read_bytes() == remaining
    assert decisions() == observed
    assert objects(native) == {key: value for key, value in before.items() if key != "closed"}
    assert not (root / "active.json").exists()


def test_native_compensating_retry_is_closed_only_after_leaving_learning_window(native, monkeypatch):
    native.append(record("rejected", action="error"))
    with monkeypatch.context() as patch:
        patch.setattr(turn_feedback.time, "time", lambda: OLD + 1)
        assert turn_feedback.reset_rejected_for_query("query") == 1
    retry = next(key for key in objects(native) if key.startswith("strato3-retry-"))
    assert objects(native)[retry].roots == (RootKind.OPEN_FEEDBACK,)
    native.tail()
    obj = objects(native)[retry]
    assert obj.state is NodeState.CLOSED
    native.owner.delete(obj.identity, obj.version)
    assert native.owner.version(obj.identity) is None


@pytest.mark.parametrize("gap", ["warning", "effects_missing", "tutor_failure", "unknown_effect",
                                "demote_failure", "recent_duplicate"])
def test_uncertain_effects_and_repeat_clicks_never_become_closed_by_age(native, gap):
    value = record("kept")
    if gap == "warning":
        value["warning"] = "turn_not_found"
    elif gap == "effects_missing":
        del value["effects"]
    elif gap == "tutor_failure":
        value["tutor_feedback_status"] = "failed"
    elif gap == "unknown_effect":
        value["effects"] = [{"type": "new-effect"}]
    elif gap == "demote_failure":
        value["effects"] = [{"type": "feedback_demote", "action": "refused"}]
    native.append(value)
    native.tail()
    if gap == "recent_duplicate":
        native.append(record("kept", action="repeat"))
    obj = objects(native)["kept"]
    assert obj.state is NodeState.OPEN and obj.roots
    before = native.path.read_bytes()
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.owner.delete(obj.identity, obj.version)
    assert native.path.read_bytes() == before


@pytest.mark.parametrize("change", ["append", "foreign", "window"])
def test_custody_version_and_window_are_rechecked(native, change):
    native.append(record("closed", ts=datetime.now(timezone.utc).timestamp() if change == "window" else OLD))
    native.tail()
    obj = objects(native)["closed"]
    identity = obj.identity
    if change == "append":
        native.append(record("closed", warning="turn_not_found"))
    elif change == "foreign":
        identity = replace(identity, store=(native.path.parent / "foreign").as_uri())
    before = native.path.read_bytes()
    with pytest.raises(RetentionError):
        native.owner.delete(identity, obj.version)
    assert native.path.read_bytes() == before


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SIGKILL and durable JSONL replacement")
def test_interruption_after_replace_preserves_learning_and_original_receipt(native, tmp_path):
    native.append(record("closed"))
    remaining = native.tail()
    observed = decisions()
    obj = objects(native)["closed"]
    owners = {native.owner.name: native.owner}
    root, public, factory = collection(tmp_path, (obj,), owners)
    journal = root / (RUN[7:] + ".sqlite")
    with sqlite3.connect(journal) as db:
        original = db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall()
    rename = os.replace
    child = os.fork()
    if child == 0:
        try:
            def interrupted(source, destination, **kwargs):
                rename(source, destination, **kwargs)
                if str(source).startswith(_TEMP_PREFIX):
                    os.kill(os.getpid(), signal.SIGKILL)
            os.replace = interrupted
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert native.path.read_bytes() == remaining and decisions() == observed
    with sqlite3.connect(journal) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == original
        assert db.execute("SELECT status FROM intents").fetchall() == [("deleted",)]
