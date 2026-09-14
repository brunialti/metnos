"""An accepted choice has one durable continuation, across HTTP/Telegram."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest


OWNER = "consent-test-owner"
SENDER = "http:consent-test-owner:conversation"
DIALOG = "consent-once-test"


@pytest.fixture
def pending(tmp_path, monkeypatch):
    import dialog_pending as dp
    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    dp.save_pending(SENDER, DIALOG, {
        "dialog_id": DIALOG, "owner_user_id": OWNER,
        "origin_turn_id": "origin-turn", "started_at": dp._utc_now_iso(),
        "timeout_s": 600, "step_index": 0, "values_collected": {},
        "completed": False, "cancelled": False,
        "dialog": [{"var": "decision", "schema": {"kind": "choice",
            "choices": [{"value": "session", "label": "Session"},
                        {"value": "reject", "label": "Reject"}]}}],
        "on_complete": {"type": "gate_dispatch", "owner_user_id": OWNER,
                        "branches": {"session": {"tool": "test_tool", "args": {}}}},
    })
    return dp


def complete():
    import orchestration as orch
    return orch.process_completion_callback(
        SENDER, DIALOG, owner_user_id=OWNER, channel="http")


@pytest.mark.parametrize("source", ["http_chat", "http_form_owner",
    "http_form_capability", "telegram_chat", "telegram_button"])
def test_submission_source_committed_with_value(pending, source):
    result = pending.consume_pending_step(
        SENDER, DIALOG, "decision", "session", owner_user_id=OWNER, source=source)
    assert result["completed"]
    stored = pending.load_pending(SENDER, DIALOG, owner_user_id=OWNER)
    assert stored["values_collected"] == {"decision": "session"}
    assert stored["submissions"] == {
        "decision": {"source": source, "at": stored["completed_at"]}}
    assert not pending.consume_pending_step(
        SENDER, DIALOG, "decision", "reject", owner_user_id=OWNER,
        source="telegram_button")["ok"]
    assert pending.load_pending(SENDER, DIALOG, owner_user_id=OWNER) == stored


@pytest.mark.parametrize("value", [None, "", "avvio temporaneo?", "turno:f35ee3d1"])
def test_non_choice_never_records_consent(pending, monkeypatch, value):
    import orchestration as orch
    monkeypatch.setattr(orch, "_esegui_ramo", lambda *a, **k: pytest.fail("launched"))
    assert not pending.consume_pending_step(
        SENDER, DIALOG, "decision", value, owner_user_id=OWNER,
        source="http_chat")["ok"]
    complete()
    stored = pending.load_pending(SENDER, DIALOG, owner_user_id=OWNER)
    assert not stored["completed"]
    assert not stored.get("submissions")
    assert not stored.get("callback_claimed_at")


@pytest.mark.parametrize("raises", [False, True])
def test_callback_result_and_failure_are_durable_not_reexecuted(pending, monkeypatch, raises):
    import orchestration as orch
    calls = []
    def branch(*args, **kwargs):
        calls.append(True)
        if raises:
            raise RuntimeError("private failure detail")
        return {"ok": False, "error": "launch refused"}
    monkeypatch.setattr(orch, "_esegui_ramo", branch)
    pending.consume_pending_step(SENDER, DIALOG, "decision", "session",
                                 owner_user_id=OWNER, source="http_form_owner")
    first = complete()
    assert complete() == first
    assert calls == [True]
    stored = pending.load_pending(SENDER, DIALOG, owner_user_id=OWNER)
    assert stored["callback_state"] == "completed"
    assert stored["callback_receipt"]["text"] == first.text
    assert first.turn_id == "origin-turn"
    assert "private failure detail" not in first.text


def test_concurrent_callbacks_do_not_double_dispatch(pending, monkeypatch):
    import orchestration as orch
    entered, release = Event(), Event()
    calls = []
    def branch(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return {"ok": True}
    monkeypatch.setattr(orch, "_esegui_ramo", branch)
    pending.consume_pending_step(SENDER, DIALOG, "decision", "session",
                                 owner_user_id=OWNER, source="telegram_button")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(complete)
        try:
            assert entered.wait(5)
            duplicate = pool.submit(complete).result(timeout=5)
            assert "callback_in_progress" in duplicate.text
        finally:
            release.set()
        first_result = first.result(timeout=5)
    assert calls == [True]
    assert complete() == first_result


@pytest.mark.parametrize("recorded", [False, True])
def test_unattributed_or_internal_choice_is_not_consent(pending, monkeypatch, recorded):
    import orchestration as orch
    monkeypatch.setattr(orch, "_esegui_ramo", lambda *a, **k: pytest.fail("launched"))
    pending.consume_pending_step(SENDER, DIALOG, "decision", "session", owner_user_id=OWNER)
    if not recorded:
        stored = pending.load_pending(SENDER, DIALOG, owner_user_id=OWNER)
        stored.pop("submissions")
        pending.save_pending(SENDER, DIALOG, stored)
    assert "consent_submission_unverified" in complete().text
    assert not pending.load_pending(SENDER, DIALOG, owner_user_id=OWNER).get(
        "callback_claimed_at")


@pytest.mark.parametrize("terminal", ["cancelled", "expired"])
def test_terminal_dialog_cannot_execute(pending, monkeypatch, terminal):
    import orchestration as orch
    monkeypatch.setattr(orch, "_esegui_ramo", lambda *a, **k: pytest.fail("launched"))
    pending.consume_pending_step(SENDER, DIALOG, "decision", "session",
                                 owner_user_id=OWNER, source="http_form_owner")
    stored = pending.load_pending(SENDER, DIALOG, owner_user_id=OWNER)
    if terminal == "cancelled":
        stored["cancelled"] = True
    else:
        stored["started_at"] = "2000-01-01T00:00:00Z"
    pending.save_pending(SENDER, DIALOG, stored)
    complete()
    assert not pending.load_pending(SENDER, DIALOG, owner_user_id=OWNER).get(
        "callback_claimed_at")
