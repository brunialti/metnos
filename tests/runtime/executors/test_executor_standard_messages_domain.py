from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
for path in (
    RUNTIME,
    ROOT / "executors" / "send_messages",
    ROOT / "executors" / "set_messages",
    ROOT / "executors" / "move_messages",
    ROOT / "executors" / "read_messages",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import move_messages  # noqa: E402
import read_messages  # noqa: E402
import send_messages  # noqa: E402
import set_messages  # noqa: E402
from executor_standard import STANDARD_ID, validate_for_lifecycle  # noqa: E402


ACTIVE_MANIFEST_MESSAGES = (
    "move_messages", "read_messages", "send_messages", "set_messages",
)


def _manifest(name: str) -> dict:
    path = ROOT / "executors" / name / "manifest.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_all_active_message_executors_are_declared_and_valid() -> None:
    for name in ACTIVE_MANIFEST_MESSAGES:
        manifest = _manifest(name)
        assert manifest["executor_standard"] == STANDARD_ID
        assert validate_for_lifecycle(manifest) == [], name
    assert not (ROOT / "executors" / "reply_messages" / "manifest.toml").exists()
    assert (ROOT / "executors" / "_retired" / "reply_messages").is_dir()


def test_send_dispatches_new_email(monkeypatch) -> None:
    captured = {}

    def fake_send(args):
        captured.update(args)
        return {"ok": True, "results": [{"id": "sent-1"}], "failed": []}

    monkeypatch.setattr(send_messages.email_metnos, "send", fake_send)
    out = send_messages.invoke({
        "account": "all",
        "messages": [{"to": "a@example.test", "body": "hello"}],
    })
    assert out == {
        "ok": True, "ok_count": 1, "fail_count": 0,
        "results": [{"id": "sent-1"}], "failed": [],
    }
    assert captured["messages"][0]["subject"] == "hello"


def test_send_absorbs_gmail_reply_without_recipient(monkeypatch) -> None:
    captured = {}

    def fake_reply(args):
        captured.update(args)
        return {"ok": True, "results": [{
            "id": "reply-1", "in_reply_to": args["message_id"],
            "thread_id": "thread-1",
        }], "failed": []}

    monkeypatch.setattr(send_messages.gmail_google_workspace, "reply", fake_reply)
    monkeypatch.setattr(
        send_messages.gmail_google_workspace, "send",
        lambda _args: (_ for _ in ()).throw(AssertionError("send called")),
    )
    out = send_messages.invoke({
        "account": "all",
        "messages": [{"in_reply_to": "gmail-42", "body": "confermo"}],
    })
    assert out["ok"] is True
    assert out["ok_count"] == 1
    assert captured == {"message_id": "gmail-42", "body": "confermo"}


def test_send_mixed_vector_reports_partial_failure(monkeypatch) -> None:
    monkeypatch.setattr(send_messages.email_metnos, "send", lambda _args: {
        "ok": True, "results": [{"id": "sent-1"}], "failed": [],
    })
    out = send_messages.invoke({
        "account": "all",
        "messages": [
            {"to": "a@example.test", "body": "ok"},
            {"in_reply_to": "gmail-42", "body": ""},
        ],
    })
    assert out["ok"] is False
    assert out["ok_count"] == 1
    assert out["fail_count"] == 1
    assert out["partial"] is True


def test_set_messages_rejects_non_mutating_label_listing(monkeypatch) -> None:
    monkeypatch.setattr(
        set_messages.gmail_google_workspace, "labels",
        lambda _args: (_ for _ in ()).throw(AssertionError("backend called")),
    )
    no_target = set_messages.invoke({})
    no_change = set_messages.invoke({"message_ids": ["id-1"]})
    assert no_target["error_class"] == "invalid_args"
    assert no_change["error_class"] == "invalid_args"


def test_set_and_move_dispatch_typed_vectors(monkeypatch) -> None:
    monkeypatch.setattr(set_messages.gmail_google_workspace, "labels", lambda args: {
        "ok": True, "ok_count": 1, "fail_count": 0,
        "results": [{"id": args["message_ids"][0], "labels_now": ["STARRED"]}],
    })
    changed = set_messages.invoke({"message_ids": ["g1"], "add": ["STARRED"]})
    assert changed["ok"] is True
    assert changed["results"][0]["id"] == "g1"

    seen = {}

    def fake_move(args):
        seen.update(args)
        return {"ok": True, "ok_count": 1, "fail_count": 0,
                "results": [{"uid": "7"}], "failed": []}

    monkeypatch.setattr(move_messages.email_metnos, "move", fake_move)
    moved = move_messages.invoke({"message_ids": ["7"], "dst_folder": "Trash"})
    assert moved["ok"] is True
    assert seen["uids"] == ["7"]


def test_message_dispatchers_normalize_partial_backend_results(monkeypatch) -> None:
    monkeypatch.setattr(read_messages.email_metnos, "read", lambda _args: {
        "ok": True,
        "entries": [{"uid": "1"}],
        "failed": [{"uid": "2", "error": "unreadable"}],
        "accounts": ["main"],
    })
    read = read_messages.invoke({})
    assert read["ok"] is False
    assert read["partial"] is True
    assert read["ok_count"] == read["fail_count"] == 1
    assert read["accounts"] == ["main"]

    monkeypatch.setattr(set_messages.gmail_google_workspace, "labels", lambda _args: {
        "ok": False,
        "results": [{"id": "g1", "labels_now": ["STARRED"]}],
        "failed": [{"id": "g2", "error": "not found"}],
    })
    changed = set_messages.invoke({
        "message_ids": ["g1", "g2"], "add": ["STARRED"],
    })
    assert changed["ok"] is False
    assert changed["partial"] is True

    monkeypatch.setattr(move_messages.email_metnos, "move", lambda _args: {
        "ok": False,
        "results": [{"uid": "7"}],
        "failed": [{"uid": "8", "error": "not found"}],
    })
    moved = move_messages.invoke({
        "message_ids": ["7", "8"], "dst_folder": "Trash",
    })
    assert moved["ok"] is False
    assert moved["partial"] is True
