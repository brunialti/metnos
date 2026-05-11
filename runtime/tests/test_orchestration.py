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
        msg_back = process_completion_callback("host", did, actor="host")

    payload = _cred.load("cifs_TESTHOST")
    assert payload["username"] == "alice"
    assert payload["password"] == "hunter2"
    mocked.assert_called_once()
    assert msg_back == "carta vaglio mount"


def test_process_completion_callback_dialog_not_found():
    """dialog_id sconosciuto → messaggio diagnostico non None."""
    from orchestration import process_completion_callback
    msg = process_completion_callback("nope", "ffff", actor="host")
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
                                        actor="host")
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
    msg = process_completion_callback("host", "abc", actor="host")
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
        msg = process_completion_callback("host", "exp01", actor="host")

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
        msg = process_completion_callback("host", "exp02", actor="host")

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
        msg = process_completion_callback("host", "exp03", actor="host")

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
        msg = process_completion_callback("host", "exp04", actor="host")

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
        msg = process_completion_callback("host", "exp05", actor="host")

    assert "Rilancio" in msg
