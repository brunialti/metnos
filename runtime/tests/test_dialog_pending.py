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
    older = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
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
