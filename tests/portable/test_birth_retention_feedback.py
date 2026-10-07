"""Physical compaction must preserve native feedback decisions and queue work."""
from dataclasses import asdict, replace
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
from executor_birth_feedback import (
    QuarantineCAS, apply_negative_feedback, enqueue_failure_review_inactive,
    resolve_failure_review_job,
)
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_birth_sqlite import _operational_birth_sqlite_owners
from install.birth_retention_feedback import _FeedbackOwner
from install.birth_retention_jsonl import _TEMP_PREFIX
from test_birth_retention_artifacts import RUN, collection
from test_birth_retention_turns import OLD, receipt


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
    reviews = _operational_birth_sqlite_owners(lambda: None, None)[-1]
    owner = _FeedbackOwner(path=path, reviews=reviews, require_exclusion=lambda: None, owner=None)

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

    def quarantine(turn_id, *, status=QuarantineCAS.APPLIED):
        requests = []
        def enqueue(job, request):
            requests.append((job, request))
            previous_umask = os.umask(0o077)
            try:
                return enqueue_failure_review_inactive(job, request, created_at="2020-01-01T00:00:02Z",
                                                       db_path=reviews.path)
            finally:
                os.umask(previous_umask)
        outcome = apply_negative_feedback(receipt(turn_id), failure_evidence_hash="sha256:" + "3" * 64,
            error_code="user_feedback_error", quarantine_exact=lambda *_: status,
            enqueue_idempotent=enqueue)
        effect = {"type": "feedback_quarantine", "tool": "demo", **asdict(outcome)}
        return effect, requests

    return SimpleNamespace(path=path, owner=owner, reviews=reviews,
                           append=append, tail=tail, quarantine=quarantine)


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


def test_real_failure_review_queue_must_close_before_feedback_can_be_removed(native):
    effect, _ = native.quarantine("failed")
    native.append(record("failed", action="error", effects=[effect]))
    native.tail()
    obj = objects(native)["failed"]
    queued, = native.reviews.scan()
    assert obj.roots == (RootKind.OPEN_FEEDBACK,) and obj.references == (queued.identity,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.owner.delete(obj.identity, obj.version)
    assert resolve_failure_review_job(effect["failure_job_id"], db_path=native.reviews.path)
    closed = objects(native)["failed"]
    assert closed.state is NodeState.CLOSED and not closed.references
    native.owner.delete(closed.identity, closed.version)
    assert native.owner.version(closed.identity) is None


def test_a_review_added_after_scan_is_observed_before_the_effect(native):
    effect, requests = native.quarantine("failed")
    job, request = requests[0]
    assert resolve_failure_review_job(job, db_path=native.reviews.path)
    native.append(record("failed", action="error", effects=[effect]))
    native.tail()
    closed = objects(native)["failed"]
    assert closed.state is NodeState.CLOSED
    enqueue_failure_review_inactive(job, request, created_at="2020-01-01T00:00:02Z",
                                    db_path=native.reviews.path)
    before = native.path.read_bytes()
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.owner.delete(closed.identity, closed.version)
    assert native.path.read_bytes() == before


def test_native_stale_feedback_has_no_outstanding_review(native):
    effect, requests = native.quarantine("stale", status=QuarantineCAS.STALE)
    assert not requests
    native.append(record("stale", action="error", effects=[effect]))
    native.tail()
    obj = objects(native)["stale"]
    assert obj.state is NodeState.CLOSED
    native.owner.delete(obj.identity, obj.version)


@pytest.mark.parametrize("gap", ["warning", "effects_missing", "tutor_failure", "unknown_effect",
                                "quarantine_missing", "enqueue_failure", "demote_failure",
                                "review_binding", "recent_duplicate"])
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
    elif gap == "quarantine_missing":
        value["effects"] = [{"type": "feedback_quarantine", "status": "no_execution_receipt"}]
    elif gap == "enqueue_failure":
        effect, _ = native.quarantine("kept")
        resolve_failure_review_job(effect["failure_job_id"], db_path=native.reviews.path)
        effect["status"] = "enqueue_failed"
        value["effects"] = [effect]
    elif gap == "demote_failure":
        value["effects"] = [{"type": "feedback_demote", "action": "refused"}]
    elif gap == "review_binding":
        effect, _ = native.quarantine("kept")
        effect["failure_job_id"] = "sha256:" + "0" * 64
        value["effects"] = [effect]
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


@pytest.mark.parametrize("change", ["append", "foreign", "window", "malformed_queue"])
def test_custody_version_and_window_are_rechecked(native, change):
    native.append(record("closed", ts=datetime.now(timezone.utc).timestamp() if change == "window" else OLD))
    native.tail()
    obj = objects(native)["closed"]
    identity = obj.identity
    if change == "append":
        native.append(record("closed", warning="turn_not_found"))
    elif change == "foreign":
        identity = replace(identity, store=(native.path.parent / "foreign").as_uri())
    elif change == "malformed_queue":
        native.reviews.path.write_bytes(b"unreadable")
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
