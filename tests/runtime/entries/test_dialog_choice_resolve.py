"""Test risoluzione risposta CHOICE nel dialogo (23/6/2026).

Bug live turno 2e7916f0: la disambiguazione su HTTP cade in dialogue-testo
(1 step) e la risposta utente ("1"/"email") arriva come testo libero. Prima
`consume_pending_step` la salvava GREZZA → il callback (forced_object) non
combaciava con nessuna scelta. Ora la risolve (indice/value/label) al `value`
canonico; invalido → non avanza. Generale §7.9, dominio/lingua-agnostico.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_OWNER = "pytest-runtime-owner"


@pytest.fixture
def dp(tmp_path, monkeypatch):
    import dialog_pending as _dp
    monkeypatch.setattr(_dp, "DIALOG_DIR", tmp_path / "get_inputs")
    return _dp


_STEP = {"var": "object", "prompt": "?",
         "schema": {"kind": "choice", "choices": [
             {"value": "messages", "label": "Le tue email / messaggi"},
             {"value": "dirs", "label": "Le tue cartelle"},
         ]}}


# ── _resolve_choice_reply (puro) ─────────────────────────────────────────
def test_resolve_by_index():
    import dialog_pending as dp
    assert dp._resolve_choice_reply("1", _STEP) == "messages"
    assert dp._resolve_choice_reply("2", _STEP) == "dirs"


def test_resolve_by_value():
    import dialog_pending as dp
    assert dp._resolve_choice_reply("messages", _STEP) == "messages"


def test_resolve_by_label_exact_and_substring():
    import dialog_pending as dp
    assert dp._resolve_choice_reply("Le tue cartelle", _STEP) == "dirs"
    # substring unico, case-insensitive
    assert dp._resolve_choice_reply("email", _STEP) == "messages"


def test_resolve_invalid_returns_sentinel():
    import dialog_pending as dp
    assert dp._resolve_choice_reply("xyz", _STEP) is dp._INVALID_CHOICE
    assert dp._resolve_choice_reply("9", _STEP) is dp._INVALID_CHOICE
    assert dp._resolve_choice_reply("", _STEP) is dp._INVALID_CHOICE


def test_resolve_passthrough_non_choice():
    import dialog_pending as dp
    step = {"var": "x", "schema": {"kind": "text"}}
    assert dp._resolve_choice_reply("ciao", step) == "ciao"


# ── consume_pending_step end-to-end ──────────────────────────────────────
def _choice_state(dialog_id="dc01"):
    return {
        "dialog_id": dialog_id, "title": "Disambig", "description": None,
        "dialog": [_STEP], "fmt": "dialogue", "values_collected": {},
        "step_index": 0,
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "actor": "host", "owner_user_id": _OWNER,
        "channel": "http", "timeout_s": 3600,
        "completed": False, "cancelled": False,
        "on_complete": {"type": "rerun_query_disambiguated", "query": "q"},
    }


def test_consume_resolves_index_to_value(dp):
    dp.save_pending("http:host", "dc01", _choice_state())
    res = dp.consume_pending_step(
        "http:host", "dc01", "object", "1", owner_user_id=_OWNER)
    assert res["ok"] is True
    assert res["completed"] is True
    # il VALUE canonico, non il grezzo "1"
    assert res["state"]["values_collected"]["object"] == "messages"


def test_consume_invalid_choice_does_not_advance(dp):
    dp.save_pending("http:host", "dc02", _choice_state("dc02"))
    res = dp.consume_pending_step(
        "http:host", "dc02", "object", "xyz", owner_user_id=_OWNER)
    assert res["ok"] is False
    assert res["error"] == "invalid_choice"
    # dialog ancora pending, step non avanzato → niente garbage nel callback
    state = dp.load_pending("http:host", "dc02", owner_user_id=_OWNER)
    assert state["step_index"] == 0
    assert state["completed"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
