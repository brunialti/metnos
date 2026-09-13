"""I-026: an ambiguous identity becomes an explicit choice, never a guess.

Real turn 693ac51fc169437e: find_packages returned one installed entry with
three candidate identities and no resolved one; run_processes stopped with
ambiguous_source_context before running. The engine now asks which identity
is meant; the real resume invokes the consumer once, with the chosen identity,
on the destination chosen by the runtime and in the same conversation.
Execution is simulated only at ``invoke_tool_by_name``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

OWNER = "owner-identity-choice"
CONV = "conv-identity-choice"
DEVICE = "PC-ROBERTO"

PROGRAMS_SCHEMA = {
    "type": "array",
    "from_entries_key": "resolved_id",
    "from_entries_complete": True,
    "from_entries_candidates_key": "candidates",
}
SCHEMAS = {
    "find_packages": {"properties": {"packages": {"type": "array"}}},
    "run_processes": {"properties": {"programs": PROGRAMS_SCHEMA,
                                     "lifetime": {"type": "string"}}},
    "get_processes": {"properties": {}},
}
CANDIDATES = [
    {"name": "Dropbox", "resolved_id": "Dropbox.Dropbox", "version": "200"},
    {"name": "Dropbox Beta", "resolved_id": "Dropbox.Beta", "version": "201"},
    # Same identity as the first, different case: not a separate choice.
    {"name": "Dropbox", "resolved_id": "dropbox.dropbox", "version": "200"},
    {"name": "Dropbox Passwords", "resolved_id": "Dropbox.Passwords",
     "version": "3"},
]
AMBIGUOUS = {"package_id": "dropbox", "installed": True, "source": "winget",
             "candidates": CANDIDATES}


@pytest.fixture
def dp(tmp_path, monkeypatch):
    import dialog_pending
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "dialogs")
    return dialog_pending


@pytest.fixture
def invocations(monkeypatch):
    """The only simulated boundary: every resumed invocation is recorded."""
    import agent_runtime
    import loader

    catalog = {name: SimpleNamespace(name=name, args_schema=schema,
                                     timeout_s=30)
               for name, schema in SCHEMAS.items()}
    monkeypatch.setattr(loader, "load_catalog",
                        lambda **_kw: SimpleNamespace(executors=catalog))
    monkeypatch.setattr(agent_runtime, "_engine_v2_catalog_with_builtins",
                        lambda items: list(items))
    seen = []

    def invoke(tool, args, **kwargs):
        seen.append({"tool": tool, "args": dict(args),
                     "target_device": kwargs.get("target_device")})
        return {"ok": True, "entries": [], "summary": f"{tool} done"}

    monkeypatch.setattr(agent_runtime, "invoke_tool_by_name", invoke)
    return seen


def _run(entries, *, tail=False):
    """Real engine and real bridge on find_packages -> run_processes."""
    from engine.dispatch import _inject_gate_resume_if_paused
    from engine.executor import Executor
    from engine.types import Framework, StepSpec

    calls = []

    def invoke(tool, args):
        calls.append(tool)
        if tool == "find_packages":
            return {"ok": True, "entries": entries}
        return {"ok": True}

    steps = [StepSpec(tool="find_packages", args={"packages": ["Dropbox"]}),
             StepSpec(tool="run_processes",
                      args={"from_step": 1, "lifetime": "session"})]
    if tail:
        steps.append(StepSpec(tool="get_processes", args={}))
    framework = Framework(steps=steps, final_message="")
    catalog = [SimpleNamespace(name=name, args_schema=schema)
               for name, schema in SCHEMAS.items()]
    runtime_ctx = {"actor": "host", "channel": "http",
                   "owner_user_id": OWNER, "conversation_id": CONV,
                   "target_device": DEVICE,
                   "user_query_raw": "avvia dropbox su pc-roberto"}
    run = Executor(invoke_executor=invoke, catalog=catalog).run(
        framework, query="avvia dropbox", runtime_ctx=runtime_ctx)
    _inject_gate_resume_if_paused(run, "avvia dropbox", runtime_ctx,
                                  framework=framework)
    return run, calls


def _payload(run):
    return run.steps[-1].result["needs_inputs"]


def test_projection_offers_only_distinct_valid_identities():
    from engine.executor import _resolve_from_step
    from engine.types import StepRun
    from from_step_projection import CONTEXT_ERRORS_KEY

    history = [StepRun(step_idx=1, tool="find_packages", args={},
                       result={"ok": True, "entries": [
                           {"resolved_id": "Vendor.First"}, AMBIGUOUS,
                           {"resolved_id": "Vendor.Last"}]},
                       ok=True, latency_ms=0)]
    args = _resolve_from_step({"from_step": 1}, history,
                              SCHEMAS["run_processes"])

    assert "programs" not in args
    (error,) = args[CONTEXT_ERRORS_KEY]
    assert error["reason"] == "incomplete_vector_projection"
    first, middle, last = error["slots"]
    assert (first, last) == ("Vendor.First", "Vendor.Last")
    assert [option["value"] for option in middle] == [
        "Dropbox.Dropbox", "Dropbox.Beta", "Dropbox.Passwords"]


@pytest.mark.parametrize("candidates", [
    [], [{"resolved_id": "Only.One"}],
    [{"resolved_id": "Same.Id"}, {"resolved_id": "same.id"}],
    [{"resolved_id": ""}, {"name": "no identity"}],
])
def test_fewer_than_two_identities_keeps_the_explicit_error(candidates):
    run, calls = _run([dict(AMBIGUOUS, candidates=candidates)])

    assert calls == ["find_packages"]
    result = run.steps[-1].result
    assert result["error_class"] == "ambiguous_source_context"
    assert "decision" not in result


def test_undeclared_candidates_are_never_offered():
    from engine.executor import _resolve_from_step
    from engine.types import StepRun
    from from_step_projection import CONTEXT_ERRORS_KEY

    schema = {"properties": {"programs": {
        key: value for key, value in PROGRAMS_SCHEMA.items()
        if key != "from_entries_candidates_key"}}}
    history = [StepRun(step_idx=1, tool="find_packages", args={},
                       result={"ok": True, "entries": [AMBIGUOUS]},
                       ok=True, latency_ms=0)]
    args = _resolve_from_step({"from_step": 1}, history, schema)
    assert "slots" not in args[CONTEXT_ERRORS_KEY][0]


def test_engine_asks_before_running_the_consumer():
    run, calls = _run([AMBIGUOUS])

    assert calls == ["find_packages"], "run_processes must not run"
    assert run.final_kind == "ask"
    payload = _payload(run)
    (step,) = payload["dialog"]
    assert step["schema"]["kind"] == "choice"
    labels = [choice["label"] for choice in step["schema"]["choices"]]
    assert len(set(labels)) == 3
    callback = payload["on_complete"]
    assert callback["type"] == "resume_executor_with_values"
    assert callback["executor"] == "run_processes"
    assert callback["args_base"] == {"lifetime": "session"}
    assert callback["list_args"] == {"programs": [{"var": step["var"]}]}
    # Destination and conversation come from the runtime, not an observed host.
    assert callback["target_device"] == DEVICE
    assert callback["conversation_id"] == CONV
    assert run.steps[-1].host == "server"


def test_only_offered_identities_are_accepted():
    from channels.daemon import parse_step_value

    run, _calls = _run([AMBIGUOUS])
    schema = _payload(run)["dialog"][0]["schema"]

    assert parse_step_value("Dropbox.Unknown", schema)[0] is False
    assert parse_step_value("Dropbox.Beta", schema)[1] == "Dropbox.Beta"
    assert parse_step_value("3", schema)[1] == "Dropbox.Passwords"


def test_resume_invokes_once_with_the_choice_on_the_same_device(invocations):
    import orchestration

    run, _calls = _run([AMBIGUOUS])
    payload = _payload(run)
    var = payload["dialog"][0]["var"]
    orchestration._process_resume_executor_with_values(
        payload["on_complete"], {var: "Dropbox.Beta"},
        actor="host", channel="http")

    assert invocations == [{
        "tool": "run_processes",
        "args": {"lifetime": "session", "programs": ["Dropbox.Beta"]},
        "target_device": DEVICE,
    }], "one invocation, no replay of find_packages"


def test_exact_identities_keep_their_position(invocations):
    import orchestration

    run, _calls = _run([{"resolved_id": "Vendor.First"}, AMBIGUOUS,
                        {"resolved_id": "Vendor.Last"}])
    payload = _payload(run)
    (step,) = payload["dialog"]
    orchestration._process_resume_executor_with_values(
        payload["on_complete"], {step["var"]: "Dropbox.Dropbox"},
        actor="host", channel="http")

    assert invocations[0]["args"]["programs"] == [
        "Vendor.First", "Dropbox.Dropbox", "Vendor.Last"]


def test_the_whole_tail_keeps_destination_and_conversation(invocations):
    import orchestration

    run, calls = _run([AMBIGUOUS], tail=True)
    assert calls == ["find_packages"]
    callback = _payload(run)["on_complete"]
    assert callback["type"] == "resume_executor_values_tail"
    assert callback["conversation_id"] == CONV
    assert [item["tool"] for item in callback["tail_steps"]] == [
        "get_processes"]

    var = _payload(run)["dialog"][0]["var"]
    orchestration._process_resume_executor_values_tail(
        callback, {var: "Dropbox.Passwords"}, actor="host", channel="http")

    assert [(item["tool"], item["target_device"]) for item in invocations] == [
        ("run_processes", DEVICE), ("get_processes", DEVICE)]
    assert invocations[0]["args"]["programs"] == ["Dropbox.Passwords"]


def test_cancel_runs_nothing(dp, invocations):
    import http_routes_agent as routes
    import orchestration
    from messages import get as msg

    run, _calls = _run([AMBIGUOUS])
    sender = f"http:{OWNER}:{CONV}"
    saved = orchestration.orchestrate_needs_inputs(
        run.steps[-1].result, sender_id=sender, actor="host", channel="http",
        owner_user_id=OWNER)
    assert saved.get("ok")

    reply = routes._apply_dialog_pending(
        sender, "annulla", actor="host", channel="http",
        conversation_id=CONV, owner_user_id=OWNER)

    assert reply == msg("MSG_DIALOG_CANCELLED")
    assert invocations == []


def test_telegram_buttons_complete_through_the_real_dispatcher(dp, invocations):
    """Same choice on Telegram: inline buttons, then the real completion."""
    import orchestration

    run, _calls = _run([{"resolved_id": "Vendor.First"}, AMBIGUOUS])
    sender = "telegram:host"
    saved = orchestration.orchestrate_needs_inputs(
        run.steps[-1].result, sender_id=sender, actor="host",
        channel="telegram", owner_user_id=OWNER)
    assert saved.get("ok")
    (state,) = dp.list_pending(sender, owner_user_id=OWNER)
    assert state["fmt"] == "telegram_inline"
    var = state["dialog"][0]["var"]

    consumed = dp.consume_pending_step(
        sender, state["dialog_id"], var, "Dropbox.Beta", owner_user_id=OWNER)
    assert consumed.get("completed")
    orchestration.process_completion_callback(
        sender, state["dialog_id"], actor="host", owner_user_id=OWNER,
        channel="telegram")

    assert [(item["tool"], item["args"]["programs"], item["target_device"])
            for item in invocations] == [
        ("run_processes", ["Vendor.First", "Dropbox.Beta"], DEVICE)]
