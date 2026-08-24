"""DEV-001: placement memory follows observed execution, not stale intent."""

from __future__ import annotations


def _log(*, resolved: str, result: dict):
    from agent_runtime import StepLog, TurnLog

    log = TurnLog(ts_start=0.0, user_query="q")
    log._target_context_key = "scope-v2:test"
    log._resolved_target_id = resolved
    step = StepLog(step_num=1, chosen_tool="get_processes", raw_args={})
    step.result = result
    log.steps.append(step)
    return log


def test_observed_server_execution_resets_context(monkeypatch):
    import agent_runtime
    import chat_target_store

    calls = []
    monkeypatch.setattr(
        chat_target_store, "set_last_target",
        lambda *args: calls.append(args),
    )
    agent_runtime._remember_observed_target(
        _log(resolved="server", result={"ok": True}),
    )
    assert calls == [("scope-v2:test", "server", None)]


def test_observed_remote_execution_keeps_exact_device(monkeypatch):
    import agent_runtime
    import chat_target_store

    calls = []
    monkeypatch.setattr(
        chat_target_store, "set_last_target",
        lambda *args: calls.append(args),
    )
    agent_runtime._remember_observed_target(_log(
        resolved="device-id",
        result={"ok": True, "_ran_on_device": "PC-TEST"},
    ))
    assert calls == [("scope-v2:test", "device-id", "PC-TEST")]


def test_no_execution_does_not_change_context(monkeypatch):
    import agent_runtime
    import chat_target_store

    calls = []
    monkeypatch.setattr(
        chat_target_store, "set_last_target",
        lambda *args: calls.append(args),
    )
    log = _log(resolved="server", result={"ok": True})
    log.steps.clear()
    agent_runtime._remember_observed_target(log)
    assert calls == []


def test_unconfirmed_remote_attempt_does_not_claim_server(monkeypatch):
    import agent_runtime
    import chat_target_store

    calls = []
    monkeypatch.setattr(
        chat_target_store, "set_last_target",
        lambda *args: calls.append(args),
    )
    agent_runtime._remember_observed_target(_log(
        resolved="device-id",
        result={"ok": False, "error_class": "remote_timeout"},
    ))
    assert calls == []
