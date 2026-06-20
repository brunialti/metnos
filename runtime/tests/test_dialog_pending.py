"""Test per dialog_pending storage (ADR 0090).

6 test:
  1. roundtrip save/load.
  2. consume_pending_step avanza index.
  3. completion quando step_index == total.
  4. cancel rimuove dal listing pending.
  5. var_mismatch error.
  6. cleanup_expired rimuove i file scaduti.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


@pytest.fixture
def dp(tmp_path, monkeypatch):
    """Modulo dialog_pending isolato in tmp_path."""
    import dialog_pending as _dp
    monkeypatch.setattr(_dp, "DIALOG_DIR", tmp_path / "get_inputs")
    return _dp


def _make_state(dialog_id="d0001", started_iso=None):
    return {
        "dialog_id": dialog_id,
        "title": "Test",
        "description": None,
        "dialog": [
            {"var": "a", "prompt": "?", "schema": {"kind": "text"}},
            {"var": "b", "prompt": "?", "schema": {"kind": "text"}},
        ],
        "fmt": "dialogue",
        "values_collected": {},
        "step_index": 0,
        "started_at": started_iso or datetime.now(tz=timezone.utc).isoformat(),
        "actor": "host",
        "channel": "",
        "timeout_s": 3600,
        "completed": False,
        "cancelled": False,
    }


def test_save_and_load_roundtrip(dp):
    state = _make_state()
    p = dp.save_pending("host", "d0001", state)
    assert p.exists()
    loaded = dp.load_pending("host", "d0001")
    assert loaded["dialog_id"] == "d0001"
    assert loaded["title"] == "Test"
    assert loaded["step_index"] == 0


def test_load_missing_returns_none(dp):
    assert dp.load_pending("host", "nope") is None


def test_consume_persists_sender_id_in_state(dp):
    """Roberto 20/6: il callback on_complete (resume_engine_gate) legge
    state['sender_id'] per ricaricare il pending — get_approval/get_inputs NON
    lo salvano (il sender e' la cartella). consume_pending_step lo persiste cosi'
    il resume del gate non aborta «sender_id mancante»."""
    state = _make_state()
    # nota: lo state NON ha 'sender_id' (come get_approval)
    assert "sender_id" not in state
    dp.save_pending("telegram:roberto", "d0001", state)
    cres = dp.consume_pending_step("telegram:roberto", "d0001", "a", "v1")
    assert cres["ok"]
    assert cres["state"]["sender_id"] == "telegram:roberto"


def test_find_by_dialog_id_global(dp):
    """find_by_dialog_id trova il pending GLOBALMENTE per dialog_id (uuid unico),
    a prescindere dal sender — fallback robusto quando il tap risolve un sender
    diverso da quello di salvataggio (query schedulate)."""
    # state CON sender_id esplicito → ritorna quello (caso post-consume)
    s = _make_state(dialog_id="dXYZ")
    s["sender_id"] = "telegram:roberto"
    dp.save_pending("telegram:roberto", "dXYZ", s)
    st, sender = dp.find_by_dialog_id("dXYZ")
    assert st is not None
    assert sender == "telegram:roberto"
    # il sender ritornato e' utilizzabile per ri-caricare lo stato
    assert dp.load_pending(sender, "dXYZ") is not None
    # sconosciuto → (None, None)
    assert dp.find_by_dialog_id("nope") == (None, None)


def test_find_by_dialog_id_skips_completed(dp):
    s = _make_state(dialog_id="dDONE")
    s["completed"] = True
    dp.save_pending("telegram:roberto", "dDONE", s)
    assert dp.find_by_dialog_id("dDONE") == (None, None)


def test_default_timeout_interactive_not_quickclose(dp):
    """Roberto 20/6: un dialogo single-step sì/no/scelta è INTERATTIVO →
    `FORM_TTL_S`, niente quick-close ~1 min (un gate di consenso async su
    Telegram, visto minuti dopo, non deve scadere)."""
    choice = [{"var": "decision", "schema": {"kind": "choice"}}]
    assert dp.default_timeout_for(choice) == dp.FORM_TTL_S
    assert dp.FORM_TTL_S > dp.DEFAULT_TTL_S        # generoso, non il quick-close
    # un timeout_s esplicito del chiamante resta sovrano (consent-gate = 1h)
    assert dp.is_expired({"started_at": "2000-01-01T00:00:00+00:00",
                          "timeout_s": 3600}) is True


def test_consume_step_advances_index(dp):
    dp.save_pending("host", "d1", _make_state(dialog_id="d1"))
    res = dp.consume_pending_step("host", "d1", "a", "alpha")
    assert res["ok"] is True
    assert res["step_index"] == 1
    assert res["completed"] is False
    state = res["state"]
    assert state["values_collected"] == {"a": "alpha"}


def test_consume_step_completes_when_last(dp):
    dp.save_pending("host", "d2", _make_state(dialog_id="d2"))
    dp.consume_pending_step("host", "d2", "a", "alpha")
    res = dp.consume_pending_step("host", "d2", "b", "beta")
    assert res["completed"] is True
    state = res["state"]
    assert state["values_collected"] == {"a": "alpha", "b": "beta"}
    assert state["completed"] is True


def test_var_mismatch_returns_error(dp):
    dp.save_pending("host", "d3", _make_state(dialog_id="d3"))
    res = dp.consume_pending_step("host", "d3", "wrong_var", "x")
    assert res["ok"] is False
    assert res["error"] == "var_mismatch"
    assert res["expected_var"] == "a"


def test_cancel_pending_marks_state(dp):
    dp.save_pending("host", "d4", _make_state(dialog_id="d4"))
    assert dp.cancel_pending("host", "d4") is True
    loaded = dp.load_pending("host", "d4")
    assert loaded["cancelled"] is True
    # list_pending non lo include piu'
    assert dp.list_pending("host") == []


def test_list_pending_orders_by_started(dp):
    # list_pending salta gli scaduti: i fixture di ordinamento restano "attivi"
    # perche' _make_state imposta timeout_s=3600 per-dialogo (override del
    # DEFAULT_TTL_S, oggi 60s). Il gap di 10 min verifica solo l'ordinamento.
    older = (datetime.now(tz=timezone.utc) - timedelta(minutes=10)).isoformat()
    newer = datetime.now(tz=timezone.utc).isoformat()
    dp.save_pending("host", "old", _make_state(dialog_id="old", started_iso=older))
    dp.save_pending("host", "new", _make_state(dialog_id="new", started_iso=newer))
    items = dp.list_pending("host")
    assert [d["dialog_id"] for d in items] == ["old", "new"]


def test_cleanup_expired_removes_old(dp):
    very_old = (datetime.now(tz=timezone.utc) - timedelta(hours=10)).isoformat()
    fresh = datetime.now(tz=timezone.utc).isoformat()
    s_old = _make_state(dialog_id="old", started_iso=very_old)
    s_fresh = _make_state(dialog_id="fresh", started_iso=fresh)
    s_old["timeout_s"] = 60  # 1 min, ben scaduto
    dp.save_pending("host", "old", s_old)
    dp.save_pending("host", "fresh", s_fresh)
    n = dp.cleanup_expired()
    assert n == 1
    assert dp.load_pending("host", "old") is None
    assert dp.load_pending("host", "fresh") is not None


def test_consume_idempotency_after_completion(dp):
    dp.save_pending("host", "d5", _make_state(dialog_id="d5"))
    dp.consume_pending_step("host", "d5", "a", "x")
    dp.consume_pending_step("host", "d5", "b", "y")
    # Tentativo di rifare consume su completed → error structured
    res = dp.consume_pending_step("host", "d5", "a", "z")
    assert res["ok"] is False
    assert res["error"] == "dialog_already_completed"
