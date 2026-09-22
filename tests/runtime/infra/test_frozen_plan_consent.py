"""Runtime-owned frozen plans require an authenticated one-shot form."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest


TOKEN = "a" * 64


def test_redaction_tolerates_legacy_partial_policy():
    from frozen_plan_consent import redact_args, redact_result

    executor = _executor()
    assert redact_args(executor, {"confirmation_token": TOKEN, "x": 1}) == {
        "x": 1}
    assert redact_result(executor, {"ok": True, "token": TOKEN}) == {
        "ok": True, "token": TOKEN}


def _executor():
    return SimpleNamespace(
        name="example_files", contract_id="contract-1",
        generation_id="generation-1", digest="sha256:" + "b" * 64,
        args_schema={"properties": {
            "mode": {"type": "string", "enum": ["preview", "apply"]},
            "confirmation_token": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
        }},
        capabilities=[], reverse_pattern="module.reverse",
        execution_policy={"frozen_plan": {
            "argument": "mode", "preview_value": "preview",
            "apply_value": "apply", "token_argument": "confirmation_token",
        }},
    )


@pytest.fixture
def frozen_dialog(tmp_path, monkeypatch):
    import dialog_pending as dp
    import orchestration as orch
    from frozen_plan_consent import executor_binding

    executor = _executor()
    record = {
        "executor": executor.name,
        "executor_binding": executor_binding(executor),
        "token": TOKEN,
        "args": {"mode": "apply", "confirmation_token": TOKEN,
                 "paths": [str(index) for index in range(151)]},
        "artifact_suffix": ".frozen-plan.json",
        "journal_suffix": ".receipt.json",
        "recovery": "same_token_write_ahead_v1",
        "expires_at": int(time.time()) + 600,
    }
    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    result = orch.orchestrate_frozen_plan(
        record, sender_id="http:owner", actor="owner",
        owner_user_id="owner", channel="http", origin_turn_id="original")
    return dp, result["dialog_id"], result, executor


@pytest.mark.parametrize(
    "source", ["internal", "http_chat", "telegram_chat", "telegram_button"])
def test_non_form_sources_cannot_accept(frozen_dialog, source):
    dp, dialog_id, result, _executor_row = frozen_dialog
    assert result["fmt"] == "form"
    denied = dp.consume_pending_step(
        "http:owner", dialog_id, "decision", "apply",
        owner_user_id="owner", source=source)
    assert denied["error"] == "form_only"
    state = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    assert not state["completed"]
    assert not state.get("submissions")


def test_exact_owner_turn_channel_args_and_replay(frozen_dialog, monkeypatch):
    import agent_runtime
    import loader
    import orchestration as orch
    from frozen_plan_consent import grant_environment

    dp, dialog_id, _result, executor = frozen_dialog
    assert not dp.consume_pending_step(
        "http:owner", dialog_id, "decision", "apply",
        owner_user_id="outsider", source="http_form_owner")["ok"]
    assert dp.consume_pending_step(
        "http:owner", dialog_id, "decision", "apply",
        owner_user_id="owner", source="http_form_owner")["completed"]

    calls = []

    def invoke(tool, args, **kwargs):
        calls.append(dict(args))
        assert len(args["paths"]) == 151  # never reconstruct a truncated list
        assert kwargs["turn_id"] == "original"
        assert grant_environment(
            executor, args, owner_user_id="owner", actor="owner",
            channel="http", turn_id="original") == {
                "METNOS_FROZEN_PLAN_AUTHORIZATION": TOKEN}
        assert not grant_environment(
            executor, args, owner_user_id="owner", actor="owner",
            channel="telegram", turn_id="original")
        assert not grant_environment(
            executor, {**args, "paths": []}, owner_user_id="owner",
            actor="owner", channel="http", turn_id="original")
        return {"ok": True, "summary": "Applied", "results": []}

    monkeypatch.setattr(loader, "load_catalog", lambda **_kwargs:
                        SimpleNamespace(executors={executor.name: executor}))
    monkeypatch.setattr(orch, "_frozen_plan_principal",
                        lambda _state: ({"id": "owner"}, ""))
    monkeypatch.setattr(agent_runtime, "invoke_tool_by_name", invoke)

    wrong_actor = orch.process_completion_callback(
        "http:owner", dialog_id, actor="other", channel="http",
        owner_user_id="owner")
    assert "used" in wrong_actor.text.lower() or wrong_actor.text
    assert not calls

    first = orch.process_completion_callback(
        "http:owner", dialog_id, actor="owner", channel="http",
        owner_user_id="owner")
    second = orch.process_completion_callback(
        "http:owner", dialog_id, actor="owner", channel="http",
        owner_user_id="owner")
    assert first == second
    assert first.text == "Applied"
    assert len(calls) == 1
    assert not grant_environment(
        executor, calls[0], owner_user_id="owner", actor="owner",
        channel="http", turn_id="original")


def test_dialog_lock_rejects_attacker_symlink(tmp_path, monkeypatch):
    import dialog_pending as dp

    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    sender_dir = dp.DIALOG_DIR / "sender"
    sender_dir.mkdir(parents=True)
    target = tmp_path / "target"
    target.write_text("untouched", encoding="utf-8")
    (sender_dir / ".dialog.lock").symlink_to(target)
    with pytest.raises(OSError):
        dp.consume_pending_step(
            "sender", "dialog", "choice", "yes", owner_user_id="owner")
    assert target.read_text(encoding="utf-8") == "untouched"


def test_telegram_preserves_authenticated_https_form_url(
        frozen_dialog, monkeypatch, tmp_path):
    import http_auth
    from channels.inline_ui import keyboard_for_proposal
    from channels.telegram_markup import inline_keyboard
    from dialog_capability import verify
    from urllib.parse import parse_qs, urlsplit

    _dp, dialog_id, result, _executor_row = frozen_dialog
    key = tmp_path / "admin.key"
    key.write_text("test-secret", encoding="utf-8")
    monkeypatch.setattr(http_auth, "ADMIN_KEY_PATH", key)
    monkeypatch.setenv("METNOS_PUBLIC_ORIGIN", "https://metnos.example")
    buttons, preview = keyboard_for_proposal(
        result["expandable_caps"][0], sender_candidates=["http:owner"],
        owner_user_id="owner")
    assert preview is None
    serialized = inline_keyboard(buttons)
    button = serialized["inline_keyboard"][0][0]
    assert "url" in button and "callback_data" not in button
    parsed = urlsplit(button["url"])
    assert parsed.scheme == "https"
    assert parsed.path == f"/agent/dialog/{dialog_id}/form"
    capability = parse_qs(parsed.query)["cap"][0]
    assert verify(dialog_id, capability, "test-secret")
    assert not verify("other-dialog", capability, "test-secret")


@pytest.mark.parametrize("origin", [
    "http://metnos.example", "https://user@metnos.example",
    "https://metnos.example/agent", "https://metnos.example?next=x",
    "https://metnos.example#fragment",
])
def test_telegram_form_url_rejects_non_origin_values(
        frozen_dialog, monkeypatch, tmp_path, origin):
    import http_auth
    from channels.inline_ui import keyboard_for_proposal

    _dp, _dialog_id, result, _executor_row = frozen_dialog
    key = tmp_path / "admin.key"
    key.write_text("test-secret", encoding="utf-8")
    monkeypatch.setattr(http_auth, "ADMIN_KEY_PATH", key)
    monkeypatch.setenv("METNOS_PUBLIC_ORIGIN", origin)
    buttons, preview = keyboard_for_proposal(
        result["expandable_caps"][0], sender_candidates=["http:owner"],
        owner_user_id="owner")
    assert buttons is None and preview is None


@pytest.mark.parametrize("ack,expected", [
    ({"ok": True, "sent_message_id": "42"}, "delivered"),
    ({"ok": False, "error": "offline"}, "pending"),
    ({"ok": False, "delivery_ambiguous": True}, "ambiguous"),
])
def test_telegram_delivery_outbox_is_durable_and_truthful(
        tmp_path, monkeypatch, ack, expected):
    import dialog_pending as dp
    import orchestration as orch
    import channels.telegram
    import durable_workloads.events

    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    state = {
        "dialog_id": "delivery", "sender_id": "telegram:owner",
        "owner_user_id": "owner", "actor": "owner", "channel": "telegram",
        "completed": True, "cancelled": False, "started_at": "",
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce"},
        "callback_receipt": {"text": "Applied"},
        "callback_delivery_state": "pending",
    }
    dp.save_pending("telegram:owner", "delivery", state)
    monkeypatch.setattr(
        durable_workloads.events, "resolve_telegram_recipient",
        lambda _owner: "chat-1")

    class Channel:
        def __init__(self, **_kwargs):
            pass

        def send(self, **_kwargs):
            return dict(ack)

    monkeypatch.setattr(channels.telegram, "TelegramChannel", Channel)
    orch._deliver_frozen_receipt(
        "telegram:owner", "delivery", "nonce", owner_user_id="owner")
    stored = dp.load_pending(
        "telegram:owner", "delivery", owner_user_id="owner")
    assert stored["callback_delivery_state"] == expected
    if expected == "pending":
        assert dp.pending_callback_deliveries()[0]["dialog_id"] == "delivery"
    else:
        assert dp.pending_callback_deliveries() == []


def test_dead_delivery_worker_becomes_ambiguous_and_is_not_retried(
        tmp_path, monkeypatch):
    import dialog_pending as dp

    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    state = {
        "dialog_id": "delivery", "sender_id": "telegram:owner",
        "owner_user_id": "owner", "channel": "telegram", "completed": True,
        "cancelled": False, "started_at": "",
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce"},
        "callback_receipt": {"text": "Applied"},
        "callback_delivery_state": "running",
        "callback_delivery_process": {
            "boot_id": "gone", "pid": 99999999, "start_ticks": "0"},
    }
    dp.save_pending("telegram:owner", "delivery", state)
    claimed = dp.begin_callback_delivery(
        "telegram:owner", "delivery", "nonce", owner_user_id="owner")
    assert claimed["status"] == "ambiguous"
    assert dp.pending_callback_deliveries() == []
    current = dp.load_pending(
        "telegram:owner", "delivery", owner_user_id="owner")
    current["callback_delivery_state"] = "running"
    current["callback_delivery_process"] = dp._process_claim()
    dp.save_pending("telegram:owner", "delivery", current)
    assert dp.begin_callback_delivery(
        "telegram:owner", "delivery", "nonce",
        owner_user_id="owner")["status"] == "in_progress"


def test_definitive_delivery_failure_is_retried_by_outbox(
        tmp_path, monkeypatch):
    import dialog_pending as dp
    import orchestration as orch
    import channels.telegram
    import durable_workloads.events

    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    dp.save_pending("telegram:owner", "delivery", {
        "dialog_id": "delivery", "sender_id": "telegram:owner",
        "owner_user_id": "owner", "channel": "telegram",
        "completed": True, "cancelled": False, "started_at": "",
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce"},
        "callback_receipt": {"text": "Applied"},
        "callback_delivery_state": "pending",
    })
    monkeypatch.setattr(
        durable_workloads.events, "resolve_telegram_recipient",
        lambda _owner: "chat-1")
    acknowledgements = iter((
        {"ok": False, "error": "offline"},
        {"ok": True, "sent_message_id": "42"},
    ))

    class Channel:
        def __init__(self, **_kwargs):
            pass

        def send(self, **_kwargs):
            return next(acknowledgements)

    monkeypatch.setattr(channels.telegram, "TelegramChannel", Channel)
    orch._deliver_frozen_receipt(
        "telegram:owner", "delivery", "nonce", owner_user_id="owner")
    assert dp.load_pending(
        "telegram:owner", "delivery",
        owner_user_id="owner")["callback_delivery_state"] == "pending"
    assert orch.retry_pending_callback_deliveries() == {"attempted": 1}
    final = dp.load_pending(
        "telegram:owner", "delivery", owner_user_id="owner")
    assert final["callback_delivery_state"] == "delivered"
    assert final["callback_delivery_attempts"] == 2


def test_delivery_exception_is_terminally_ambiguous(tmp_path, monkeypatch):
    import dialog_pending as dp
    import orchestration as orch
    import channels.telegram
    import durable_workloads.events

    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    dp.save_pending("telegram:owner", "delivery", {
        "dialog_id": "delivery", "sender_id": "telegram:owner",
        "owner_user_id": "owner", "channel": "telegram",
        "completed": True, "cancelled": False, "started_at": "",
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce"},
        "callback_receipt": {"text": "Applied"},
        "callback_delivery_state": "pending",
    })
    monkeypatch.setattr(
        durable_workloads.events, "resolve_telegram_recipient",
        lambda _owner: "chat-1")

    class Channel:
        def __init__(self, **_kwargs):
            pass

        def send(self, **_kwargs):
            raise TimeoutError("ack lost")

    monkeypatch.setattr(channels.telegram, "TelegramChannel", Channel)
    orch._deliver_frozen_receipt(
        "telegram:owner", "delivery", "nonce", owner_user_id="owner")
    final = dp.load_pending(
        "telegram:owner", "delivery", owner_user_id="owner")
    assert final["callback_delivery_state"] == "ambiguous"
    assert dp.pending_callback_deliveries() == []
