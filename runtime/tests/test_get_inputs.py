"""Test per l'executor `get_inputs` (ADR 0090, 4-5/5/2026).

Copertura:
  1. Validazione schema dialog (vuoto, var duplicate, kind invalido, ...).
  2. Happy path single-step e multi-step.
  3. fmt='auto' decide form/dialogue secondo channel + n_steps.
  4. Lookup di dialogo esistente (cap-pending retrieval pattern).
  5. consume_pending_step avanza index e marca completed.
  6. Cancel.

Run con `python3 -m pytest runtime/tests/test_get_inputs.py -v`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
_EXECUTORS = _RUNTIME.parent / "executors" / "get_inputs"
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXECUTORS))


@pytest.fixture(autouse=True)
def isolate_dialog_dir(tmp_path, monkeypatch):
    """Isola lo storage dei dialoghi in tmp_path (un test, una dir vergine)."""
    import dialog_pending
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "get_inputs")
    yield


@pytest.fixture
def gi():
    """Import del modulo executor (lazy per non far fallire la collection se
    PYTHONPATH non e' ancora settato)."""
    import importlib
    import get_inputs as _gi
    importlib.reload(_gi)
    return _gi


# ── Validazione schema ────────────────────────────────────────────────

class TestValidation:
    def test_missing_title(self, gi):
        r = gi.invoke({})
        assert r["ok"] is False
        assert "title" in r["error"]

    def test_empty_dialog(self, gi):
        r = gi.invoke({"title": "x", "dialog": []})
        assert r["ok"] is False
        assert "almeno uno step" in r["error"]

    def test_var_duplicate(self, gi):
        r = gi.invoke({
            "title": "x",
            "dialog": [
                {"var": "y", "prompt": "?", "schema": {"kind": "text"}},
                {"var": "y", "prompt": "??", "schema": {"kind": "text"}},
            ],
        })
        assert r["ok"] is False
        assert "duplicate" in r["error"]

    def test_kind_invalid(self, gi):
        r = gi.invoke({
            "title": "x",
            "dialog": [{"var": "y", "prompt": "?", "schema": {"kind": "weirdo"}}],
        })
        assert r["ok"] is False
        assert "kind" in r["error"]

    def test_var_non_snake_case_rejected(self, gi):
        r = gi.invoke({
            "title": "x",
            "dialog": [{"var": "1bad", "prompt": "?", "schema": {"kind": "text"}}],
        })
        assert r["ok"] is False
        assert "snake_case" in r["error"]

    def test_choice_requires_choices(self, gi):
        r = gi.invoke({
            "title": "x",
            "dialog": [{"var": "y", "prompt": "?", "schema": {"kind": "choice"}}],
        })
        assert r["ok"] is False
        assert "choices" in r["error"]

    def test_too_many_steps(self, gi):
        r = gi.invoke({
            "title": "x",
            "dialog": [
                {"var": f"v{i}", "prompt": "?", "schema": {"kind": "text"}}
                for i in range(40)
            ],
        })
        assert r["ok"] is False
        assert "step" in r["error"]


# ── Happy path ────────────────────────────────────────────────────────

class TestHappyPath:
    def test_single_step_text(self, gi):
        r = gi.invoke({
            "title": "Quale citta'?",
            "dialog": [{"var": "city", "prompt": "Citta':",
                         "schema": {"kind": "text"}}],
        })
        assert r["ok"] is True
        assert r["decision"] == "input_required"
        assert r["step_total"] == 1
        assert r["step_index"] == 0
        assert len(r["dialog_id"]) == 16
        assert r["expandable_caps"][0]["kind"] == "get_inputs_response"
        assert "Step 1/1" in r["final_message_hint"]

    def test_credentials_two_step(self, gi):
        r = gi.invoke({
            "title": "Credenziali NAS",
            "description": "Server CIFS - saranno cifrate.",
            "dialog": [
                {"var": "username", "prompt": "User:",
                 "schema": {"kind": "text"}},
                {"var": "password", "prompt": "Pwd:",
                 "schema": {"kind": "credentials", "secret": True}},
            ],
            "fmt": "dialogue",
        })
        assert r["ok"] is True
        assert r["step_total"] == 2
        assert r["fmt"] == "dialogue"
        # La menzione "mascherata" e' di cortesia: il primo step e' text
        # (username), quindi NON dovrebbe esserci hint di masking. Solo lo
        # step credentials lo otterrebbe se fosse il primo.
        hint = r["final_message_hint"]
        assert "User:" in hint
        assert "Step 1/2" in hint


# ── fmt resolution ────────────────────────────────────────────────────

class TestFormatResolution:
    def test_auto_http_3steps_picks_form(self, gi):
        os.environ["METNOS_CHANNEL"] = "http"
        try:
            r = gi.invoke({
                "title": "Setup",
                "dialog": [
                    {"var": f"v{i}", "prompt": f"step {i}",
                     "schema": {"kind": "text"}}
                    for i in range(3)
                ],
                "fmt": "auto",
            })
            assert r["fmt"] == "form"
            assert "form" in r["final_message_hint"].lower()
        finally:
            del os.environ["METNOS_CHANNEL"]

    def test_auto_telegram_3steps_picks_dialogue(self, gi):
        os.environ["METNOS_CHANNEL"] = "telegram"
        try:
            r = gi.invoke({
                "title": "Setup",
                "dialog": [
                    {"var": f"v{i}", "prompt": f"step {i}",
                     "schema": {"kind": "text"}}
                    for i in range(3)
                ],
                "fmt": "auto",
            })
            assert r["fmt"] == "dialogue"
        finally:
            del os.environ["METNOS_CHANNEL"]

    def test_voice_degrades_to_dialogue(self, gi):
        r = gi.invoke({
            "title": "x",
            "dialog": [{"var": "y", "prompt": "?",
                         "schema": {"kind": "text"}}],
            "fmt": "voice",
        })
        # Voice non implementato: stub → degrada
        assert r["fmt"] == "dialogue"


# ── consume + retrieval ───────────────────────────────────────────────

class TestConsumeRetrieval:
    def test_consume_advances_then_completes(self, gi):
        import dialog_pending
        r = gi.invoke({
            "title": "Test",
            "dialog": [
                {"var": "a", "prompt": "?", "schema": {"kind": "text"}},
                {"var": "b", "prompt": "?", "schema": {"kind": "text"}},
            ],
        })
        did = r["dialog_id"]
        # consume step 1
        s1 = dialog_pending.consume_pending_step("host", did, "a", "first")
        assert s1["ok"] is True
        assert s1["step_index"] == 1
        assert s1["completed"] is False
        # consume step 2 → completed
        s2 = dialog_pending.consume_pending_step("host", did, "b", "second")
        assert s2["completed"] is True
        assert s2["state"]["values_collected"] == {"a": "first", "b": "second"}
        # Re-invoke get_inputs(dialog_id=...) ritorna i values
        r2 = gi.invoke({"dialog_id": did})
        assert r2["decision"] == "completed"
        assert r2["values"] == {"a": "first", "b": "second"}

    def test_cancel_then_lookup(self, gi):
        import dialog_pending
        r = gi.invoke({
            "title": "Test",
            "dialog": [{"var": "x", "prompt": "?", "schema": {"kind": "text"}}],
        })
        did = r["dialog_id"]
        assert dialog_pending.cancel_pending("host", did) is True
        r2 = gi.invoke({"dialog_id": did})
        assert r2["decision"] == "cancelled"

    def test_var_mismatch_rejected(self, gi):
        import dialog_pending
        r = gi.invoke({
            "title": "Test",
            "dialog": [
                {"var": "first", "prompt": "?", "schema": {"kind": "text"}},
                {"var": "second", "prompt": "?", "schema": {"kind": "text"}},
            ],
        })
        did = r["dialog_id"]
        # Tenta di consumare il SECONDO step prima del primo
        res = dialog_pending.consume_pending_step("host", did, "second", "x")
        assert res["ok"] is False
        assert res["error"] == "var_mismatch"
        assert res["expected_var"] == "first"


# ── Timeout / args extra ──────────────────────────────────────────────

class TestArgs:
    def test_timeout_invalid(self, gi):
        r = gi.invoke({
            "title": "x",
            "dialog": [{"var": "y", "prompt": "?", "schema": {"kind": "text"}}],
            "timeout_s": 99999,
        })
        assert r["ok"] is False
        assert "timeout_s" in r["error"]
