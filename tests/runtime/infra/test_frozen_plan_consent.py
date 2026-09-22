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
            "confirmation_token": {
                "type": "string", "pattern": "^[0-9a-f]{64}$",
                "runtime_resolved": True,
            },
            "paths": {"type": "array", "items": {"type": "string"}},
        }},
        capabilities=[], reverse_pattern="module.reverse",
        execution_policy={"frozen_plan": {
            "argument": "mode", "preview_value": "preview",
            "apply_value": "apply", "token_argument": "confirmation_token",
            "token_result": "confirmation_token",
            "carry_arguments": ["paths"],
            "artifact_suffix": ".frozen-plan.json",
            "journal_suffix": ".receipt.json",
            "recovery": "same_token_write_ahead_v1",
        }},
    )


def _record(executor, *, item_count=151):
    from frozen_plan_consent import prepare_resume

    paths = [str(index) for index in range(item_count)]
    record = prepare_resume(
        executor, {"mode": "preview", "paths": paths}, {
            "ok": True,
            "confirmation_token": TOKEN,
            "expires_at": int(time.time()) + 600,
            "source_count": item_count,
            "move_count": item_count,
            "duplicate_count": 0,
            "results": [
                {"action": "move", "source": f"/source/{index}",
                 "destination": f"/destination/{index}"}
                for index in range(item_count)
            ],
        })
    assert record is not None
    return record


@pytest.fixture
def frozen_dialog(tmp_path, monkeypatch):
    import dialog_pending as dp
    import orchestration as orch
    executor = _executor()
    record = _record(executor)
    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    result = orch.orchestrate_frozen_plan(
        record, sender_id="http:owner", actor="owner",
        owner_user_id="owner", channel="http", origin_turn_id="original")
    return dp, result["dialog_id"], result, executor


def test_form_discloses_bound_actions_digest_and_explicit_truncation(
        frozen_dialog):
    dp, dialog_id, result, _executor_row = frozen_dialog
    state = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    description = str(state.get("description") or result.get("description") or "")
    serialized_public = repr(result)
    assert "plan_sha256=" in description
    assert "actions_shown=50 actions_total=151 truncated=true" in description
    assert '"action":"move"' in description
    assert '"source":"/source/0"' in description
    assert '"destination":"/destination/0"' in description
    assert TOKEN not in description
    assert TOKEN not in serialized_public
    record = state["on_complete"]["record"]
    assert record["consent_preview"]["shown_count"] == 50
    assert record["consent_preview"]["total_count"] == 151


def test_tampered_consent_projection_cannot_resume(frozen_dialog, monkeypatch):
    import agent_runtime
    import orchestration as orch

    dp, dialog_id, _result, _executor_row = frozen_dialog
    state = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    state["on_complete"]["record"]["consent_preview"]["items"][0][
        "destination"] = "/attacker"
    dp.save_pending("http:owner", dialog_id, state)
    assert dp.consume_pending_step(
        "http:owner", dialog_id, "decision", "apply",
        owner_user_id="owner", source="http_form_owner")["completed"]
    monkeypatch.setattr(orch, "_frozen_plan_principal",
                        lambda _state: ({"id": "owner"}, ""))
    monkeypatch.setattr(
        agent_runtime, "invoke_tool_by_name",
        lambda *_args, **_kwargs: pytest.fail("tampered form invoked executor"))
    outcome = orch.process_completion_callback(
        "http:owner", dialog_id, actor="owner", channel="http",
        owner_user_id="owner")
    assert outcome.text


def test_telegram_rebinding_blocks_apply(tmp_path, monkeypatch):
    import dialog_pending as dp
    import orchestration as orch
    import pairing
    import users

    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    result = orch.orchestrate_frozen_plan(
        _record(_executor(), item_count=1),
        sender_id="telegram:owner", actor="owner", owner_user_id="owner",
        channel="telegram", origin_turn_id="original",
        conversation_id="chat-a")
    dialog_id = result["dialog_id"]
    assert dp.consume_pending_step(
        "telegram:owner", dialog_id, "decision", "apply",
        owner_user_id="owner", source="http_form_owner")["completed"]
    monkeypatch.setattr(users, "get_user", lambda _owner: {
        "id": "owner", "autonomy_level": "supervised"})
    monkeypatch.setattr(users, "get_channel", lambda *_args: {
        "recipient_id": "chat-b", "verified_at": "now"})
    monkeypatch.setattr(users, "find_user_by_recipient", lambda *_args: {
        "id": "owner"})
    monkeypatch.setattr(pairing, "get_pairing", lambda *_args:
                        SimpleNamespace(autonomy_level="supervised"))
    outcome = orch.process_completion_callback(
        "telegram:owner", dialog_id, actor="owner", channel="telegram",
        owner_user_id="owner")
    assert outcome.text
    state = dp.load_pending(
        "telegram:owner", dialog_id, owner_user_id="owner")
    assert state["callback_state"] == "completed"


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
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce",
                        "conversation_id": "chat-1"},
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


def test_telegram_rebinding_never_delivers_receipt(tmp_path, monkeypatch):
    import channels.telegram
    import dialog_pending as dp
    import durable_workloads.events
    import orchestration as orch

    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    dp.save_pending("telegram:owner", "delivery", {
        "dialog_id": "delivery", "sender_id": "telegram:owner",
        "owner_user_id": "owner", "actor": "owner", "channel": "telegram",
        "completed": True, "cancelled": False, "started_at": "",
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce",
                        "conversation_id": "chat-a"},
        "callback_receipt": {"text": "Applied"},
        "callback_delivery_state": "pending",
    })
    monkeypatch.setattr(
        durable_workloads.events, "resolve_telegram_recipient",
        lambda _owner: "chat-b")

    class Channel:
        def __init__(self, **_kwargs):
            pass

        def send(self, **_kwargs):
            pytest.fail("receipt disclosed to rebound Telegram recipient")

    monkeypatch.setattr(channels.telegram, "TelegramChannel", Channel)
    orch._deliver_frozen_receipt(
        "telegram:owner", "delivery", "nonce", owner_user_id="owner")
    final = dp.load_pending(
        "telegram:owner", "delivery", owner_user_id="owner")
    assert final["callback_delivery_state"] == "failed"
    assert final["callback_delivery_ack"]["error"] == "telegram_origin_changed"
    assert dp.pending_callback_deliveries() == []


def test_builtin_apply_cannot_bypass_frozen_plan_grant(monkeypatch):
    import agent_runtime
    from frozen_plan_consent import grant

    executor = _executor()
    executor.name = "frozen_builtin"
    calls = []
    monkeypatch.setitem(agent_runtime._BUILTIN_TOOL_HANDLERS, executor.name,
                        lambda _args: {"ok": True})
    monkeypatch.setattr(
        agent_runtime, "_invoke_builtin_handler",
        lambda _tool, args, **_kwargs:
            calls.append(dict(args)) or {"ok": True})
    apply_args = {"mode": "apply", "confirmation_token": TOKEN,
                  "paths": ["one"]}
    denied = agent_runtime.invoke_tool_by_name(
        executor.name, apply_args, catalog=[executor], actor="owner",
        channel="http", owner_user_id="owner", turn_id="turn")
    assert denied["error_code"] == "frozen_plan_authorization_required"
    assert calls == []
    record = _record(executor, item_count=1)
    with grant(record, owner_user_id="owner", actor="owner",
               channel="http", turn_id="turn"):
        forbidden = agent_runtime.invoke_tool_by_name(
            executor.name, record["args"], catalog=[executor], actor="owner",
            channel="http", owner_user_id="owner", turn_id="turn")
    assert forbidden["error_code"] == "frozen_plan_in_process_apply_forbidden"
    assert calls == []

    preview_args = {"mode": "preview", "paths": ["one"]}
    preview = agent_runtime.invoke_tool_by_name(
        executor.name, preview_args, catalog=[executor], actor="owner",
        channel="http", owner_user_id="owner", turn_id="turn")
    assert preview["ok"] is True
    assert calls == [preview_args]


def test_verb_unique_apply_cannot_bypass_frozen_plan_grant(monkeypatch):
    import agent_runtime
    import loader
    from frozen_plan_consent import grant

    executor = _executor()
    executor.name = "frozen_verb_unique"
    monkeypatch.setitem(loader.VERB_UNIQUE_REGISTRY, executor.name, {
        "expose_to_planner": True, "module": SimpleNamespace()})
    monkeypatch.setattr(loader, "boot_register_verb_unique_builtins", lambda: None)
    invoked = []
    monkeypatch.setattr(
        loader, "invoke_verb_unique",
        lambda *_args, **_kwargs: invoked.append(True) or {"ok": True})
    denied = agent_runtime.invoke_tool_by_name(
        executor.name,
        {"mode": "apply", "confirmation_token": TOKEN, "paths": ["one"]},
        catalog=[executor], actor="owner", channel="http",
        owner_user_id="owner", turn_id="turn")
    assert denied["error_code"] == "frozen_plan_authorization_required"
    assert invoked == []

    record = _record(executor, item_count=1)
    with grant(record, owner_user_id="owner", actor="owner",
               channel="http", turn_id="turn"):
        forbidden = agent_runtime.invoke_tool_by_name(
            executor.name, record["args"], catalog=[executor], actor="owner",
            channel="http", owner_user_id="owner", turn_id="turn")
    assert forbidden["error_code"] == "frozen_plan_in_process_apply_forbidden"
    assert invoked == []

    preview = agent_runtime.invoke_tool_by_name(
        executor.name, {"mode": "preview", "paths": ["one"]},
        catalog=[executor], actor="owner", channel="http",
        owner_user_id="owner", turn_id="turn")
    assert preview["ok"] is True
    assert invoked == [True]


def test_dead_delivery_worker_becomes_ambiguous_and_is_not_retried(
        tmp_path, monkeypatch):
    import dialog_pending as dp

    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    state = {
        "dialog_id": "delivery", "sender_id": "telegram:owner",
        "owner_user_id": "owner", "channel": "telegram", "completed": True,
        "cancelled": False, "started_at": "",
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce",
                        "conversation_id": "chat-1"},
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
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce",
                        "conversation_id": "chat-1"},
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
        "on_complete": {"type": "resume_frozen_plan", "nonce": "nonce",
                        "conversation_id": "chat-1"},
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


def test_interrupted_apply_is_recovered_with_same_frozen_record(
        frozen_dialog, monkeypatch):
    """A dead child leaves no terminal lie and the scheduler reconciles it."""
    import agent_runtime
    import frozen_plan_consent
    import loader
    import orchestration as orch

    dp, dialog_id, _result, executor = frozen_dialog
    assert dp.consume_pending_step(
        "http:owner", dialog_id, "decision", "apply",
        owner_user_id="owner", source="http_form_owner")["completed"]
    monkeypatch.setattr(loader, "load_catalog", lambda **_kwargs:
                        SimpleNamespace(executors={executor.name: executor}))
    monkeypatch.setattr(orch, "_frozen_plan_principal",
                        lambda _state: ({"id": "owner"}, ""))
    monkeypatch.setattr(frozen_plan_consent, "recovery_evidence",
                        lambda *_args, **_kwargs: True)
    results = iter((
        {"ok": False, "error_class": "non_json"},
        {"ok": True, "summary": "Applied", "results": []},
    ))
    calls = []

    def invoke(_tool, args, **_kwargs):
        calls.append(dict(args))
        return next(results)

    monkeypatch.setattr(agent_runtime, "invoke_tool_by_name", invoke)
    first = orch.process_completion_callback(
        "http:owner", dialog_id, actor="owner", channel="http",
        owner_user_id="owner")
    assert first.text
    interrupted = dp.load_pending(
        "http:owner", dialog_id, owner_user_id="owner")
    assert interrupted["callback_state"] == "recovery_pending"
    assert "callback_receipt" not in interrupted
    assert interrupted["callback_authorized_at"]
    assert [item["dialog_id"] for item in dp.pending_frozen_callbacks()] == [
        dialog_id]

    assert orch.retry_pending_frozen_callbacks() == {
        "attempted": 1, "completed": 1}
    final = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    assert final["callback_state"] == "completed"
    assert final["callback_receipt"]["text"] == "Applied"
    assert calls[0] == calls[1]


def test_authorized_recovery_survives_form_expiry_without_reauthorizing(
        frozen_dialog, monkeypatch):
    import agent_runtime
    import frozen_plan_consent
    import loader
    import orchestration as orch

    dp, dialog_id, _result, executor = frozen_dialog
    assert dp.consume_pending_step(
        "http:owner", dialog_id, "decision", "apply",
        owner_user_id="owner", source="http_form_owner")["completed"]
    monkeypatch.setattr(loader, "load_catalog", lambda **_kwargs:
                        SimpleNamespace(executors={executor.name: executor}))
    monkeypatch.setattr(orch, "_frozen_plan_principal",
                        lambda _state: ({"id": "owner"}, ""))
    monkeypatch.setattr(frozen_plan_consent, "recovery_evidence",
                        lambda *_args, **_kwargs: True)
    responses = iter((
        {"ok": False, "error_class": "execution_interrupted"},
        {"ok": True, "summary": "Recovered", "results": []},
    ))
    monkeypatch.setattr(agent_runtime, "invoke_tool_by_name",
                        lambda *_args, **_kwargs: next(responses))
    orch.process_completion_callback(
        "http:owner", dialog_id, actor="owner", channel="http",
        owner_user_id="owner")
    state = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    state["started_at"] = "2000-01-01T00:00:00Z"
    state["on_complete"]["record"]["expires_at"] = 1
    dp.save_pending("http:owner", dialog_id, state)
    monkeypatch.setattr(
        orch, "_frozen_plan_principal",
        lambda _state: pytest.fail("recovery asked for a new principal"))

    assert orch.retry_pending_frozen_callbacks() == {
        "attempted": 1, "completed": 1}
    final = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    assert final["callback_receipt"]["text"] == "Recovered"


def test_terminal_receipt_save_failure_returns_processing_and_retries(
        frozen_dialog, monkeypatch):
    import agent_runtime
    import frozen_plan_consent
    import loader
    import orchestration as orch

    dp, dialog_id, _result, executor = frozen_dialog
    assert dp.consume_pending_step(
        "http:owner", dialog_id, "decision", "apply",
        owner_user_id="owner", source="http_form_owner")["completed"]
    monkeypatch.setattr(loader, "load_catalog", lambda **_kwargs:
                        SimpleNamespace(executors={executor.name: executor}))
    monkeypatch.setattr(orch, "_frozen_plan_principal",
                        lambda _state: ({"id": "owner"}, ""))
    monkeypatch.setattr(frozen_plan_consent, "recovery_evidence",
                        lambda *_args, **_kwargs: True)
    calls = []

    def invoke(_tool, args, **_kwargs):
        calls.append(dict(args))
        return {"ok": True, "summary": "Applied", "results": []}

    monkeypatch.setattr(agent_runtime, "invoke_tool_by_name", invoke)
    original_save = dp.save_pending
    failed = False

    def fail_first_terminal_save(sender_id, pending_id, state):
        nonlocal failed
        if (not failed and state.get("callback_state") == "completed"
                and isinstance(state.get("callback_receipt"), dict)):
            failed = True
            raise OSError("simulated terminal receipt persistence failure")
        return original_save(sender_id, pending_id, state)

    monkeypatch.setattr(dp, "save_pending", fail_first_terminal_save)
    first = orch.process_completion_callback(
        "http:owner", dialog_id, actor="owner", channel="http",
        owner_user_id="owner")
    assert first.text != "Applied"
    interrupted = dp.load_pending(
        "http:owner", dialog_id, owner_user_id="owner")
    assert interrupted["callback_state"] == "recovery_pending"
    assert interrupted["callback_recovery_reason"] == "receipt_commit_failed"
    assert "callback_receipt" not in interrupted

    assert orch.retry_pending_frozen_callbacks() == {
        "attempted": 1, "completed": 1}
    final = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    assert final["callback_state"] == "completed"
    assert final["callback_receipt"]["text"] == "Applied"
    assert len(calls) == 2


def test_sweep_preserves_incomplete_authorized_callback(
        frozen_dialog, monkeypatch):
    import dialog_pending as dp

    _module, dialog_id, _result, _executor_row = frozen_dialog
    assert dp.consume_pending_step(
        "http:owner", dialog_id, "decision", "apply",
        owner_user_id="owner", source="http_form_owner")["completed"]
    state = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    nonce = state["on_complete"]["nonce"]
    assert dp.begin_callback_once(
        "http:owner", dialog_id, nonce,
        owner_user_id="owner")["status"] == "claimed"
    assert dp.authorize_callback_recovery(
        "http:owner", dialog_id, nonce, "binding",
        owner_user_id="owner")
    assert dp.defer_callback_recovery(
        "http:owner", dialog_id, nonce, owner_user_id="owner",
        reason="child died")
    state = dp.load_pending("http:owner", dialog_id, owner_user_id="owner")
    state["started_at"] = "2026-01-01T00:00:00Z"
    state["completed_at"] = "2026-01-01T00:00:01Z"
    state["timeout_s"] = 1
    dp.save_pending("http:owner", dialog_id, state)
    now = time.mktime((2026, 1, 1, 1, 0, 0, 0, 0, 0))
    dp.sweep_expired(now_ts=now)
    assert dp.load_pending(
        "http:owner", dialog_id, owner_user_id="owner") is not None


def test_completed_frozen_dialog_without_receipt_is_processing():
    from http_routes_agent import _dialog_lifecycle

    assert _dialog_lifecycle({
        "completed": True,
        "on_complete": {"type": "resume_frozen_plan"},
    }) == "processing"
