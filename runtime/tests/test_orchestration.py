"""Test del modulo runtime/orchestration.py (ADR 0091).

invoke_get_inputs_internal() + process_completion_callback() come unita'
isolate; orchestrate_needs_inputs() come dispatcher.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Storage isolati per dialog_pending + credentials + admin key."""
    import dialog_pending
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR",
                          tmp_path / "get_inputs")
    import credentials as _cred
    monkeypatch.setattr(_cred, "ADMIN_KEY_PATH",
                          tmp_path / "admin.key")
    monkeypatch.setattr(_cred, "CRED_DIR",
                          tmp_path / "credentials_store")
    import secrets
    (tmp_path / "admin.key").write_text(secrets.token_hex(32))
    yield tmp_path


# ── invoke_get_inputs_internal ──────────────────────────────────────


def test_invoke_get_inputs_internal_creates_pending_state(isolated_dirs):
    """Salva un dialog_pending con i campi attesi e ritorna decision input_required."""
    import dialog_pending
    from orchestration import invoke_get_inputs_internal

    dialog = [
        {"var": "username", "prompt": "User:", "schema": {"kind": "text"}},
        {"var": "password", "prompt": "Pwd:",
         "schema": {"kind": "credentials", "secret": True}},
    ]
    res = invoke_get_inputs_internal(
        sender_id="host",
        title="Credenziali test",
        description="descrizione",
        dialog=dialog,
        fmt="auto",
        on_complete={"type": "save_credentials_and_resume"},
        actor="host",
        channel=None,
    )
    assert res["ok"] is True
    assert res["decision"] == "input_required"
    assert res["step_total"] == 2
    assert res["fmt"] == "dialogue"  # auto + no http → dialogue

    state = dialog_pending.load_pending("host", res["dialog_id"])
    assert state is not None
    assert state["title"] == "Credenziali test"
    assert state["sender_id"] == "host"
    assert state["on_complete"]["type"] == "save_credentials_and_resume"


def test_invoke_get_inputs_internal_http_3steps_uses_form(isolated_dirs):
    """fmt='auto' su HTTP con >=3 step → form."""
    from orchestration import invoke_get_inputs_internal
    dialog = [
        {"var": "a", "prompt": "A:", "schema": {"kind": "text"}},
        {"var": "b", "prompt": "B:", "schema": {"kind": "text"}},
        {"var": "c", "prompt": "C:", "schema": {"kind": "text"}},
    ]
    res = invoke_get_inputs_internal(
        sender_id="http:host:_",
        title="Setup",
        description=None,
        dialog=dialog,
        fmt="auto",
        actor="host",
        channel="http",
    )
    assert res["fmt"] == "form"
    assert "/agent/dialog/" in res["final_message_hint"]


def test_invoke_get_inputs_internal_explicit_form_keeps_form(isolated_dirs):
    """fmt='form' esplicito anche con 1 step."""
    from orchestration import invoke_get_inputs_internal
    dialog = [{"var": "x", "prompt": "X:", "schema": {"kind": "text"}}]
    res = invoke_get_inputs_internal(
        sender_id="host",
        title="X",
        description=None,
        dialog=dialog,
        fmt="form",
        actor="host",
        channel="http",
    )
    assert res["fmt"] == "form"


def test_invoke_get_inputs_internal_voice_degrades_to_dialogue(isolated_dirs):
    """fmt='voice' stub: degrada a dialogue."""
    from orchestration import invoke_get_inputs_internal
    dialog = [{"var": "x", "prompt": "X:", "schema": {"kind": "text"}}]
    res = invoke_get_inputs_internal(
        sender_id="host", title="X", description=None,
        dialog=dialog, fmt="voice", actor="host", channel=None,
    )
    assert res["fmt"] == "dialogue"


def test_resume_executor_gate_replays_branch_then_only_tail(monkeypatch):
    from types import SimpleNamespace
    import agent_runtime
    import loader
    from orchestration import (CompletionResult,
                               _process_resume_executor_gate_tail)

    executors = {
        name: SimpleNamespace(name=name, timeout_s=30, args_schema={})
        for name in ("act_sites", "login_sites")
    }
    catalog = SimpleNamespace(executors=executors)
    monkeypatch.setattr(loader, "load_catalog", lambda **_kw: catalog)
    calls = []

    def invoke(executor, args, **_kw):
        calls.append((executor.name, dict(args)))
        if executor.name == "act_sites":
            return {"ok": True, "results": [{
                "session_id": "sid-1", "ok": True, "executed": True}]}
        return {"ok": True, "entries": [{
            "session_id": "sid-1", "logged_in": True}],
            "final_message_hint": "Accesso completato."}

    monkeypatch.setattr(agent_runtime, "invoke_executor", invoke)
    callback = {
        "gate_approve_value": "approve",
        "gate_on_approve": {"tool": "act_sites", "args": {
            "session_ids": ["sid-1"],
            "approval_tokens": {"sid-1": "opaque"}}},
        "tail_steps": [
            {"tool": "login_sites", "args": {"from_step": 1}},
            {"tool": "final_answer", "args": {}},
        ],
        "original_query": "accedi a x.test",
    }
    out = _process_resume_executor_gate_tail(
        callback, {"decision": "approve"}, actor="alice", channel="http")
    assert isinstance(out, CompletionResult)
    assert out.text == "Accesso completato."
    assert [name for name, _args in calls] == ["act_sites", "login_sites"]
    assert calls[1][1]["entries"][0]["session_id"] == "sid-1"


def test_resume_executor_gate_tail_invokes_universal_builtin(monkeypatch):
    """Regressione turn:520a574f — «Executor describe_entries non in catalog».
    Una coda post-gate che contiene un helper universale builtin
    (`describe_entries`) NON deve fallire con `tool_unknown`: il resume usa lo
    stesso dispatch del loop principale (builtin-first via registro), non solo
    il catalog degli executor firmati."""
    from types import SimpleNamespace
    import agent_runtime
    import loader
    from orchestration import (CompletionResult,
                               _process_resume_executor_gate_tail)

    # Catalog dei soli executor firmati: describe_entries NON c'e' (e' builtin).
    executors = {
        name: SimpleNamespace(name=name, timeout_s=30, args_schema={})
        for name in ("read_sites", "login_sites")
    }
    monkeypatch.setattr(loader, "load_catalog", lambda **_kw: SimpleNamespace(
        executors=executors))

    # Il branch NON si auto-presenta (nessun final_message_hint): cosi' l'engine
    # esegue davvero lo step describe_entries della coda invece di saltarlo.
    def invoke(executor, args, **_kw):
        return {"ok": True, "entries": [{"device": "phone", "active": True}]}
    monkeypatch.setattr(agent_runtime, "invoke_executor", invoke)

    builtin_calls = []
    def fake_builtin(tool_name, args, **_kw):
        builtin_calls.append(tool_name)
        return {"ok": True, "entries": args.get("entries") or [],
                "final_message_hint": "1 dispositivo attivo."}
    monkeypatch.setattr(agent_runtime, "_invoke_builtin_handler", fake_builtin)

    callback = {
        "gate_approve_value": "approve",
        "gate_on_approve": {"tool": "read_sites", "args": {
            "session_ids": ["sid-1"], "approval_tokens": {"sid-1": "opaque"}}},
        "tail_steps": [
            {"tool": "describe_entries", "args": {"from_step": 1}},
            {"tool": "final_answer", "args": {}},
        ],
        "original_query": "login a 192.168.1.10 e dimmi i device attivi",
    }
    out = _process_resume_executor_gate_tail(
        callback, {"decision": "approve"}, actor="host", channel="http")

    assert isinstance(out, CompletionResult)
    # describe_entries e' stato invocato via handler builtin, non rifiutato.
    assert "describe_entries" in builtin_calls
    assert "non in catalog" not in (out.text or "")


def test_resume_executor_secret_values_replays_branch_then_tail(monkeypatch):
    from types import SimpleNamespace
    import agent_runtime
    import loader
    from orchestration import (CompletionResult,
                               _process_resume_executor_values_tail)

    executors = {
        name: SimpleNamespace(name=name, timeout_s=30, args_schema={})
        for name in ("login_sites", "act_sites")
    }
    monkeypatch.setattr(loader, "load_catalog", lambda **_kw: SimpleNamespace(
        executors=executors))
    calls = []

    def invoke(executor, args, **_kw):
        calls.append((executor.name, dict(args)))
        if executor.name == "login_sites":
            return {"ok": True, "entries": [{
                "session_id": "sid-otp", "logged_in": True}]}
        return {"ok": True, "results": [{
            "session_id": "sid-otp", "executed": True}],
            "final_message_hint": "Prenotazione trovata."}

    monkeypatch.setattr(agent_runtime, "invoke_executor", invoke)
    callback = {
        "executor": "login_sites",
        "args_base": {
            "session_ids": ["sid-otp"],
            "_otp_session_vars": {"one_time_code": "sid-otp"},
        },
        "tail_steps": [
            {"tool": "act_sites", "args": {
                "from_step": 1, "action": "cerca prenotazioni"}},
            {"tool": "final_answer", "args": {}},
        ],
        "original_query": "accedi e cerca prenotazioni",
    }

    out = _process_resume_executor_values_tail(
        callback, {"one_time_code": "123456"},
        actor="alice", channel="http")

    assert isinstance(out, CompletionResult)
    assert out.text == "Prenotazione trovata."
    assert [name for name, _args in calls] == ["login_sites", "act_sites"]
    assert calls[0][1]["one_time_code"] == "123456"
    assert calls[1][1]["entries"] == [{
        "session_id": "sid-otp", "logged_in": True}]


def test_resume_executor_gate_carries_tail_across_repeated_gates(
        monkeypatch, isolated_dirs):
    from types import SimpleNamespace
    import agent_runtime
    import dialog_pending
    import loader
    from orchestration import (CompletionResult,
                               _process_resume_executor_gate_tail)

    executors = {
        name: SimpleNamespace(name=name, timeout_s=30, args_schema={})
        for name in ("login_sites", "act_sites")
    }
    monkeypatch.setattr(loader, "load_catalog", lambda **_kw: SimpleNamespace(
        executors=executors))
    calls = []
    login_results = [
        {"ok": True, "decision": "input_required", "dialog_id": "nested-2",
         "final_message_hint": "Approva ancora\n\n"
                               "INLINE_FORM:/agent/dialog/nested-2/form"},
        {"ok": True, "entries": [{
            "session_id": "sid-1", "logged_in": True}]},
    ]

    def invoke(executor, args, **_kw):
        calls.append((executor.name, dict(args)))
        if executor.name == "login_sites":
            return login_results.pop(0)
        return {"ok": True, "results": [{
            "session_id": "sid-1", "executed": True}],
            "final_message_hint": "Ricerca completata."}

    monkeypatch.setattr(agent_runtime, "invoke_executor", invoke)
    dialog_pending.save_pending("http:alice", "nested-2", {
        "dialog_id": "nested-2", "completed": False,
        "on_complete": {
            "type": "gate_dispatch", "approve_value": "approve",
            "on_approve": {"tool": "login_sites", "args": {
                "session_ids": ["sid-1"],
                "_approval_tokens": {"sid-1": "opaque-2"}}},
        },
    })
    first_callback = {
        "gate_approve_value": "approve",
        "gate_on_approve": {"tool": "login_sites", "args": {
            "session_ids": ["sid-1"],
            "_approval_tokens": {"sid-1": "opaque-1"}}},
        "tail_steps": [
            {"tool": "act_sites", "args": {
                "from_step": 1, "action": "cerca fatture"}},
            {"tool": "final_answer", "args": {}},
        ],
        "tail_final_message": "${step2.@table}",
        "original_query": "accedi e cerca fatture",
        "conversation_id": "conv-1",
    }

    first = _process_resume_executor_gate_tail(
        first_callback, {"decision": "approve"},
        actor="alice", channel="http")
    assert isinstance(first, CompletionResult)
    assert "INLINE_FORM:/agent/dialog/nested-2/form" in first.text
    assert [name for name, _args in calls] == ["login_sites"]

    nested_state = dialog_pending.load_pending("http:alice", "nested-2")
    nested_callback = nested_state["on_complete"]
    assert nested_callback["type"] == "resume_executor_gate_tail"
    assert nested_callback["tail_steps"] == first_callback["tail_steps"]
    assert nested_callback["conversation_id"] == "conv-1"

    second = _process_resume_executor_gate_tail(
        nested_callback, {"decision": "approve"},
        actor="alice", channel="http")
    assert isinstance(second, CompletionResult)
    assert second.text == "Ricerca completata."
    assert [name for name, _args in calls] == [
        "login_sites", "login_sites", "act_sites"]
    assert calls[-1][1]["entries"] == [{
        "session_id": "sid-1", "logged_in": True}]


def test_invoke_get_inputs_internal_rejects_empty_dialog(isolated_dirs):
    from orchestration import invoke_get_inputs_internal
    res = invoke_get_inputs_internal(
        sender_id="host", title="X", description=None,
        dialog=[], fmt="auto", actor="host",
    )
    assert res["ok"] is False
    assert "dialog vuoto" in res["error"]


def test_invoke_get_inputs_internal_rejects_empty_title(isolated_dirs):
    from orchestration import invoke_get_inputs_internal
    res = invoke_get_inputs_internal(
        sender_id="host", title="", description=None,
        dialog=[{"var": "a", "prompt": "A:", "schema": {"kind": "text"}}],
        fmt="auto", actor="host",
    )
    assert res["ok"] is False


# ── process_completion_callback ─────────────────────────────────────


def test_process_completion_callback_save_credentials_and_resume(isolated_dirs):
    """Salva credenziali + invoca resume_call=admin con args."""
    import credentials as _cred
    import dialog_pending
    from orchestration import (
        invoke_get_inputs_internal, process_completion_callback,
    )

    on_complete = {
        "type": "save_credentials_and_resume",
        "credentials_domain": "cifs_TESTHOST",
        "credentials_context": {"binding": "cifs", "host": "TESTHOST"},
        "resume_call": "admin",
        "resume_args": {
            "intent": "mount cifs",
            "command_proposed": "sudo mount -t cifs //TESTHOST/x /tmp/y",
            "credentials_domain": "cifs_TESTHOST",
        },
    }
    res = invoke_get_inputs_internal(
        sender_id="host", title="Creds", description=None,
        dialog=[
            {"var": "username", "prompt": "U:", "schema": {"kind": "text"}},
            {"var": "password", "prompt": "P:",
             "schema": {"kind": "credentials", "secret": True}},
        ],
        fmt="dialogue", on_complete=on_complete, actor="host",
    )
    did = res["dialog_id"]
    dialog_pending.consume_pending_step("host", did, "username", "alice")
    dialog_pending.consume_pending_step("host", did, "password", "hunter2")

    with mock.patch("loader.invoke_verb_unique") as mocked:
        mocked.return_value = {"ok": True, "summary": "carta vaglio mount"}
        msg_back = process_completion_callback("host", did, actor="host").text

    payload = _cred.load("cifs_TESTHOST")
    assert payload["username"] == "alice"
    assert payload["password"] == "hunter2"
    mocked.assert_called_once()
    assert msg_back == "carta vaglio mount"


def test_process_completion_callback_dialog_not_found():
    """dialog_id sconosciuto → messaggio diagnostico non None."""
    from orchestration import process_completion_callback
    msg = process_completion_callback("nope", "ffff", actor="host").text
    assert "non trovato" in msg


def test_process_completion_callback_dialog_not_completed(isolated_dirs):
    """dialog presente ma non completato → ritorna messaggio."""
    from orchestration import (
        invoke_get_inputs_internal, process_completion_callback,
    )
    res = invoke_get_inputs_internal(
        sender_id="host", title="X", description=None,
        dialog=[{"var": "a", "prompt": "A:", "schema": {"kind": "text"}}],
        fmt="dialogue", actor="host",
    )
    msg = process_completion_callback("host", res["dialog_id"],
                                        actor="host").text
    assert "non ancora completo" in msg


def test_process_completion_callback_unknown_type(isolated_dirs):
    """on_complete con type non gestito → messaggio diagnostico."""
    import dialog_pending
    from orchestration import process_completion_callback

    state = {
        "dialog_id": "abc",
        "title": "X",
        "description": None,
        "dialog": [{"var": "a", "prompt": "A:", "schema": {"kind": "text"}}],
        "fmt": "dialogue",
        "values_collected": {"a": "v"},
        "step_index": 1,
        "started_at": "2026-05-05T00:00:00+00:00",
        "actor": "host", "channel": "", "timeout_s": 600,
        "completed": True, "cancelled": False,
        "on_complete": {"type": "do_random_thing"},
    }
    dialog_pending.save_pending("host", "abc", state)
    msg = process_completion_callback("host", "abc", actor="host").text
    assert "do_random_thing" in msg


# ── orchestrate_needs_inputs ────────────────────────────────────────


def test_orchestrate_needs_inputs_dispatches_to_get_inputs(isolated_dirs):
    """observation con decision='needs_inputs' → invoke_get_inputs_internal."""
    from orchestration import orchestrate_needs_inputs
    obs = {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": {
            "title": "Test",
            "description": None,
            "dialog": [{"var": "a", "prompt": "A:",
                         "schema": {"kind": "text"}}],
            "fmt": "auto",
            "on_complete": {"type": "save_credentials_and_resume"},
        },
    }
    res = orchestrate_needs_inputs(obs, sender_id="host", actor="host")
    assert res["ok"] is True
    assert res["decision"] == "input_required"


def test_orchestrate_needs_inputs_rejects_wrong_decision(isolated_dirs):
    from orchestration import orchestrate_needs_inputs
    obs = {"decision": "approval_required"}
    res = orchestrate_needs_inputs(obs, sender_id="host")
    assert res["ok"] is False
    assert "needs_inputs" in res["error"]


# ── expand_cap_and_resume callback (FIX 1, 6/5/2026) ────────────────


def test_process_completion_callback_expand_cap_and_resume_yes(isolated_dirs):
    """confirm=True → invoke_executor con args estesi, ritorna preview."""
    import dialog_pending
    from orchestration import process_completion_callback

    state = {
        "dialog_id": "exp01",
        "title": "Allargamento",
        "description": None,
        "dialog": [{"var": "confirm", "prompt": "ok?",
                    "schema": {"kind": "yes_no"}}],
        "fmt": "dialogue",
        "values_collected": {"confirm": True},
        "step_index": 1,
        "started_at": "2026-05-06T00:00:00+00:00",
        "actor": "host", "channel": "", "timeout_s": 600,
        "completed": True, "cancelled": False,
        "on_complete": {
            "type": "expand_cap_and_resume",
            "executor": "get_processes",
            "cap_field": "top",
            "cap_suggested": 1000,
            "args_suggested": {"top": 1000, "filters": []},
            "preview_label": "processi",
        },
    }
    dialog_pending.save_pending("host", "exp01", state)

    fake_res = {
        "ok": True,
        "n_entries": 487,
        "entries": [{"name": "python", "pid": 1, "cpu_pct": 50.0},
                     {"name": "llama-server", "pid": 2, "cpu_pct": 7.7}],
    }
    fake_ex = mock.Mock()
    fake_ex.timeout_s = 30
    fake_catalog = mock.Mock()
    fake_catalog.executors = {"get_processes": fake_ex}

    with mock.patch("loader.load_catalog", return_value=fake_catalog), \
         mock.patch("agent_runtime.invoke_executor", return_value=fake_res) as mocked:
        msg = process_completion_callback("host", "exp01", actor="host").text

    mocked.assert_called_once()
    call_args = mocked.call_args
    assert call_args[0][0] is fake_ex
    assert call_args[0][1] == {"top": 1000, "filters": []}
    assert "Rilancio con top=1000" in msg
    assert "487 processi" in msg
    assert "python" in msg


def test_process_completion_callback_expand_cap_and_resume_no(isolated_dirs):
    """confirm=False → no-op, no invoke."""
    import dialog_pending
    from orchestration import process_completion_callback

    state = {
        "dialog_id": "exp02",
        "title": "Allargamento",
        "description": None,
        "dialog": [{"var": "confirm", "prompt": "ok?",
                    "schema": {"kind": "yes_no"}}],
        "fmt": "dialogue",
        "values_collected": {"confirm": False},
        "step_index": 1,
        "started_at": "2026-05-06T00:00:00+00:00",
        "actor": "host", "channel": "", "timeout_s": 600,
        "completed": True, "cancelled": False,
        "on_complete": {
            "type": "expand_cap_and_resume",
            "executor": "get_processes",
            "cap_field": "top",
            "cap_suggested": 1000,
            "args_suggested": {"top": 1000},
            "preview_label": "processi",
        },
    }
    dialog_pending.save_pending("host", "exp02", state)

    with mock.patch("loader.load_catalog") as mocked_lc:
        msg = process_completion_callback("host", "exp02", actor="host").text

    mocked_lc.assert_not_called()
    assert "troncato" in msg.lower() or "lasciato" in msg.lower()


def test_process_completion_callback_expand_cap_invoke_failure(isolated_dirs):
    """invoke_executor che ritorna ok:false → messaggio di errore."""
    import dialog_pending
    from orchestration import process_completion_callback

    state = {
        "dialog_id": "exp03",
        "title": "X", "description": None,
        "dialog": [{"var": "confirm", "prompt": "ok?",
                    "schema": {"kind": "yes_no"}}],
        "fmt": "dialogue",
        "values_collected": {"confirm": True},
        "step_index": 1, "started_at": "2026-05-06T00:00:00+00:00",
        "actor": "host", "channel": "", "timeout_s": 600,
        "completed": True, "cancelled": False,
        "on_complete": {
            "type": "expand_cap_and_resume",
            "executor": "get_processes",
            "cap_field": "top",
            "cap_suggested": 1000,
            "args_suggested": {"top": 1000},
        },
    }
    dialog_pending.save_pending("host", "exp03", state)

    fake_ex = mock.Mock(); fake_ex.timeout_s = 30
    fake_catalog = mock.Mock(); fake_catalog.executors = {"get_processes": fake_ex}

    with mock.patch("loader.load_catalog", return_value=fake_catalog), \
         mock.patch("agent_runtime.invoke_executor",
                     return_value={"ok": False, "error": "boom"}):
        msg = process_completion_callback("host", "exp03", actor="host").text

    assert "fallito" in msg.lower()
    assert "boom" in msg


def test_process_completion_callback_expand_cap_executor_missing(isolated_dirs):
    """executor non in catalog → messaggio diagnostico, no crash."""
    import dialog_pending
    from orchestration import process_completion_callback

    state = {
        "dialog_id": "exp04",
        "title": "X", "description": None,
        "dialog": [{"var": "confirm", "prompt": "?",
                    "schema": {"kind": "yes_no"}}],
        "fmt": "dialogue",
        "values_collected": {"confirm": True},
        "step_index": 1, "started_at": "2026-05-06T00:00:00+00:00",
        "actor": "host", "channel": "", "timeout_s": 600,
        "completed": True, "cancelled": False,
        "on_complete": {
            "type": "expand_cap_and_resume",
            "executor": "ghost_executor",
            "cap_field": "top",
            "cap_suggested": 1000,
            "args_suggested": {"top": 1000},
        },
    }
    dialog_pending.save_pending("host", "exp04", state)

    fake_catalog = mock.Mock(); fake_catalog.executors = {}
    with mock.patch("loader.load_catalog", return_value=fake_catalog):
        msg = process_completion_callback("host", "exp04", actor="host").text

    assert "ghost_executor" in msg
    assert "non in catalog" in msg


def test_process_completion_callback_expand_cap_string_yes_tolerant(isolated_dirs):
    """confirm passato come stringa 'si'/'sì' è tollerato (defensive)."""
    import dialog_pending
    from orchestration import process_completion_callback

    state = {
        "dialog_id": "exp05",
        "title": "X", "description": None,
        "dialog": [{"var": "confirm", "prompt": "?",
                    "schema": {"kind": "yes_no"}}],
        "fmt": "dialogue",
        "values_collected": {"confirm": "sì"},
        "step_index": 1, "started_at": "2026-05-06T00:00:00+00:00",
        "actor": "host", "channel": "", "timeout_s": 600,
        "completed": True, "cancelled": False,
        "on_complete": {
            "type": "expand_cap_and_resume",
            "executor": "get_processes",
            "cap_field": "top",
            "cap_suggested": 1000,
            "args_suggested": {"top": 1000},
        },
    }
    dialog_pending.save_pending("host", "exp05", state)

    fake_ex = mock.Mock(); fake_ex.timeout_s = 30
    fake_catalog = mock.Mock(); fake_catalog.executors = {"get_processes": fake_ex}
    fake_res = {"ok": True, "n_entries": 5, "entries": [{"name": "x"}]}

    with mock.patch("loader.load_catalog", return_value=fake_catalog), \
         mock.patch("agent_runtime.invoke_executor", return_value=fake_res):
        msg = process_completion_callback("host", "exp05", actor="host").text

    assert "Rilancio" in msg
