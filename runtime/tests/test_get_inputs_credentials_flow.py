"""Test integrazione admin → get_inputs (orchestrazione) → credentials store
+ resume admin (ADR 0090, ADR 0091).

Simula il flow E2E della migrazione Strato 2 senza coinvolgere il PLANNER
LLM. Tre test storici riadattati al nuovo pattern (ADR 0091, 5/5/2026):

  1. admin con placeholder + dominio mancante → restituisce
     decision="needs_inputs" + payload strutturato (title, description,
     dialog 2 step, on_complete=save_credentials_and_resume).

  2. orchestrate_needs_inputs → invoke_get_inputs_internal salva un
     dialog_pending con `on_complete` persistente nello state.

  3. process_completion_callback → credentials.store cifrato + ri-invoca
     admin (resume_call): al resume admin trova dominio salvato e
     procede con carta vaglio standard (signature mount.cifs).

Run con `python3 -m pytest runtime/tests/test_get_inputs_credentials_flow.py -v`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
_EXECUTORS = _RUNTIME.parent / "executors" / "get_inputs"
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXECUTORS))


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
    # Genera admin.key (32 bytes hex)
    import secrets
    (tmp_path / "admin.key").write_text(secrets.token_hex(32))
    yield tmp_path


def test_admin_needs_inputs_returns_decision_and_payload(isolated_dirs):
    """ADR 0091: admin con placeholder + dominio NON salvato →
    decision='needs_inputs' con payload strutturato (title/description/
    dialog/on_complete)."""
    from verb_unique import admin
    res = admin.invoke(
        intent="mount cifs share",
        command_proposed=(
            "sudo mount -t cifs //192.168.1.99/share /tmp/foo -o "
            "credentials=${METNOS_CIFS_CREDS},uid=1000"
        ),
        actor="host",
    )
    assert res["decision"] == "needs_inputs"
    assert res["credentials_domain"] == "cifs_192.168.1.99"
    payload = res["needs_inputs"]
    assert payload is not None
    assert "Credenziali per cifs_192.168.1.99" in payload["title"]
    assert "binding cifs" in payload["description"]
    assert "host 192.168.1.99" in payload["description"]
    # Dialog: 2 step canonici (username text + password credentials secret)
    dialog = payload["dialog"]
    assert len(dialog) == 2
    assert dialog[0]["var"] == "username"
    assert dialog[0]["schema"]["kind"] == "text"
    assert dialog[1]["var"] == "password"
    assert dialog[1]["schema"]["kind"] == "credentials"
    assert dialog[1]["schema"]["secret"] is True
    # on_complete dichiarativo
    on_complete = payload["on_complete"]
    assert on_complete["type"] == "save_credentials_and_resume"
    assert on_complete["credentials_domain"] == "cifs_192.168.1.99"
    assert on_complete["resume_call"] == "admin"
    resume_args = on_complete["resume_args"]
    assert resume_args["credentials_domain"] == "cifs_192.168.1.99"
    assert resume_args["intent"] == "mount cifs share"
    assert "${METNOS_CIFS_CREDS}" in resume_args["command_proposed"]
    # Niente summary plain text (path principale non e' piu' UX testo)
    assert res["summary"] == ""


def test_runtime_orchestrates_get_inputs_on_needs_inputs(isolated_dirs):
    """ADR 0091: orchestrate_needs_inputs invoca get_inputs internamente,
    salva un dialog_pending con on_complete persistente."""
    import dialog_pending
    from orchestration import orchestrate_needs_inputs

    # Simula l'observation di admin
    obs = {
        "ok": True,
        "decision": "needs_inputs",
        "argv": [],
        "needs_inputs": {
            "title": "Credenziali per cifs_NAS",
            "description": "binding cifs · host NAS · le credenziali saranno cifrate.",
            "dialog": [
                {"var": "username", "prompt": "Username:",
                 "schema": {"kind": "text"}},
                {"var": "password", "prompt": "Password:",
                 "schema": {"kind": "credentials", "secret": True}},
            ],
            "fmt": "auto",
            "on_complete": {
                "type": "save_credentials_and_resume",
                "credentials_domain": "cifs_NAS",
                "credentials_context": {"binding": "cifs", "host": "NAS"},
                "resume_call": "admin",
                "resume_args": {
                    "intent": "mount cifs",
                    "command_proposed": (
                        "sudo mount -t cifs //NAS/share /tmp/foo -o "
                        "credentials=${METNOS_CIFS_CREDS},uid=1000"
                    ),
                    "credentials_domain": "cifs_NAS",
                },
            },
        },
    }
    sender_id = "host"
    res = orchestrate_needs_inputs(obs, sender_id=sender_id, actor="host",
                                     channel=None)
    assert res["ok"] is True
    assert res["decision"] == "input_required"
    assert res["fmt"] in ("dialogue", "form")
    dialog_id = res["dialog_id"]
    assert dialog_id

    # State persisto: contiene on_complete + sender_id
    state = dialog_pending.load_pending(sender_id, dialog_id)
    assert state is not None
    on_complete = state.get("on_complete")
    assert on_complete is not None
    assert on_complete["type"] == "save_credentials_and_resume"
    assert on_complete["credentials_domain"] == "cifs_NAS"
    # sender_id nel state per facilitare il callback resume
    assert state.get("sender_id") == sender_id
    # Carta UI del runtime contiene il titolo + step 1
    msg = res.get("final_message_hint") or ""
    assert "Credenziali per cifs_NAS" in msg


def test_dialog_completion_triggers_credentials_store_and_resume(isolated_dirs):
    """ADR 0091: completamento del dialogo applica on_complete →
    credentials.store + invoke_verb_unique('admin', ...). Resume admin
    visto come secondo invocazione: con dominio salvato deve avanzare
    al di la' del needs_inputs (carta vaglio o execute)."""
    import credentials as _cred
    import dialog_pending
    from orchestration import (
        orchestrate_needs_inputs, process_completion_callback,
    )

    sender_id = "host"
    actor = "host"

    # 1. simula admin che emette needs_inputs
    obs = {
        "ok": True,
        "decision": "needs_inputs",
        "argv": [],
        "needs_inputs": {
            "title": "Credenziali per cifs_192.168.1.99",
            "description": "binding cifs · host 192.168.1.99",
            "dialog": [
                {"var": "username", "prompt": "Username:",
                 "schema": {"kind": "text"}},
                {"var": "password", "prompt": "Password:",
                 "schema": {"kind": "credentials", "secret": True}},
            ],
            "fmt": "auto",
            "on_complete": {
                "type": "save_credentials_and_resume",
                "credentials_domain": "cifs_192.168.1.99",
                "credentials_context": {"binding": "cifs",
                                          "host": "192.168.1.99"},
                "resume_call": "admin",
                "resume_args": {
                    "intent": "mount cifs",
                    "command_proposed": (
                        "sudo mount -t cifs //192.168.1.99/share /tmp/foo "
                        "-o credentials=${METNOS_CIFS_CREDS},uid=1000"
                    ),
                    "credentials_domain": "cifs_192.168.1.99",
                },
            },
        },
    }
    res = orchestrate_needs_inputs(obs, sender_id=sender_id, actor=actor,
                                     channel=None)
    dialog_id = res["dialog_id"]

    # 2. Utente compila i 2 step (consume_pending_step idempotente)
    dialog_pending.consume_pending_step(sender_id, dialog_id, "username", "alice")
    dialog_pending.consume_pending_step(sender_id, dialog_id, "password", "hunter2")

    # 3. process_completion_callback: salva creds + ri-invoca admin
    # Mock invoke_verb_unique per evitare import safety/seed_bootstrap
    # nel test (preserviamo sotto la chiamata invariata).
    with mock.patch("loader.invoke_verb_unique") as mocked:
        mocked.return_value = {
            "ok": True,
            "decision": "approval_required",
            "summary": "Carta vaglio: mount.cifs richiede approvazione.",
            "approval_required": True,
        }
        msg_back = process_completion_callback(
            sender_id, dialog_id, actor=actor, channel=None,
        )

    # 4. credentials store ha le creds cifrate per il dominio target
    payload = _cred.load("cifs_192.168.1.99")
    assert payload is not None
    assert payload["username"] == "alice"
    assert payload["password"] == "hunter2"
    assert "cifs_192.168.1.99" in _cred.list_domains()

    # 5. resume_call=admin invocato con args originali
    mocked.assert_called_once()
    kwargs = mocked.call_args.kwargs
    assert kwargs["intent"] == "mount cifs"
    assert "${METNOS_CIFS_CREDS}" in kwargs["command_proposed"]
    assert kwargs["credentials_domain"] == "cifs_192.168.1.99"
    assert kwargs["caller"] == "agent_runtime"

    # 6. messaggio user-facing del callback = summary del resume admin
    assert "Carta vaglio" in msg_back
