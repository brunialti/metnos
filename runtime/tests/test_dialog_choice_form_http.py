"""Regressioni Issue-B (5/7/2026, turn 1e895534): su HTTP una SCELTA si
clicca, non si trascrive — e il resume di un READER mostra i dati.

Tre proprietà:
  1. fmt='auto' su HTTP → `form` anche a 1 SOLO step se tutti gli step sono
     cliccabili (choice/yes_no/choice_with_preview); kind testuali mono-step
     restano `dialogue`. Gemello della regola Telegram (inline keyboard), ma
     SENZA il cap-24 (un form con radio regge liste lunghe).
  2. `all_choice_like` (channels.inline_ui) è il predicato condiviso.
  3. `_shape_result_for_chat` (orchestration): un result con `entries` (§2.6 =
     READER) rende i DATI (tabella per righe-foglio, lista per dict), non il
     generico «✓ Operazione completata» (che resta per i mutanti `results`).
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))
_GI_DIR = _RUNTIME.parent / "executors" / "get_inputs"
if str(_GI_DIR) not in sys.path:
    sys.path.insert(0, str(_GI_DIR))


def _dlg_choice(n=2, kind="choice"):
    return [{"var": "file_id", "schema": {
        "kind": kind,
        "choices": [{"value": f"id{i}", "label": f"Opzione {i}"}
                    for i in range(n)]}}]


def _dlg_text():
    return [{"var": "q", "schema": {"kind": "text"}}]


# ── all_choice_like (predicato condiviso) ────────────────────────────────

def test_all_choice_like_choice_only_true():
    from channels.inline_ui import all_choice_like
    assert all_choice_like(_dlg_choice()) is True


def test_all_choice_like_text_false():
    from channels.inline_ui import all_choice_like
    assert all_choice_like(_dlg_text()) is False
    assert all_choice_like(_dlg_choice() + _dlg_text()) is False


def test_all_choice_like_no_telegram_cap():
    """30 alternative: su HTTP il form regge (nessun cap-24 Telegram)."""
    from channels.inline_ui import all_choice_like, all_inline_compatible
    d = _dlg_choice(30)
    assert all_choice_like(d) is True
    assert all_inline_compatible(d) is False  # Telegram degrada (cap 24)


def test_all_choice_like_empty_false():
    from channels.inline_ui import all_choice_like
    assert all_choice_like([]) is False
    assert all_choice_like(None) is False


# ── _decide_fmt (executor get_inputs) ────────────────────────────────────

def test_decide_fmt_http_mono_choice_is_form():
    import get_inputs as _gi
    assert _gi._decide_fmt("auto", 1, "http", _dlg_choice()) == "form"


def test_decide_fmt_http_mono_text_is_dialogue():
    import get_inputs as _gi
    assert _gi._decide_fmt("auto", 1, "http", _dlg_text()) == "dialogue"


def test_decide_fmt_telegram_unchanged():
    import get_inputs as _gi
    assert _gi._decide_fmt("auto", 1, "telegram", _dlg_choice()) == "telegram_inline"
    assert _gi._decide_fmt("auto", 1, "telegram", _dlg_choice(30)) == "dialogue"


# ── resolved_fmt (orchestration runtime-side) ────────────────────────────

def test_orchestration_http_mono_choice_resolves_form(tmp_path, monkeypatch):
    import dialog_pending
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "get_inputs")
    from orchestration import invoke_get_inputs_internal
    out = invoke_get_inputs_internal(
        sender_id="http:test", title="Scegli", description=None,
        dialog=_dlg_choice(), fmt="auto", channel="http")
    assert out.get("ok") is True
    assert out.get("fmt") == "form"


def test_orchestration_http_mono_text_stays_dialogue(tmp_path, monkeypatch):
    import dialog_pending
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "get_inputs")
    from orchestration import invoke_get_inputs_internal
    out = invoke_get_inputs_internal(
        sender_id="http:test", title="Scrivi", description=None,
        dialog=_dlg_text(), fmt="auto", channel="http")
    assert out.get("ok") is True
    assert out.get("fmt") == "dialogue"


# ── _shape_result_for_chat: reader → dati; mutante → ✓ ───────────────────

def test_shape_reader_rows_renders_table():
    from orchestration import _shape_result_for_chat
    rows = [["data", "importo"], ["2026-07-01", "10,5"], ["2026-07-02", "3"]]
    txt = _shape_result_for_chat({"ok": True, "entries": rows})
    assert "data" in txt and "2026-07-01" in txt and "|" in txt
    assert "Operazione completata" not in txt


def test_shape_reader_rows_capped_with_notice():
    from orchestration import _shape_result_for_chat
    rows = [["a", "b"]] + [[str(i), str(i)] for i in range(40)]
    txt = _shape_result_for_chat({"ok": True, "entries": rows})
    # cap §2.7: nota top-of + non tutte le 40 righe
    assert txt.count("\n") < 40
    assert "39" not in txt.splitlines()[-1] or "40" in txt.splitlines()[0]


def test_shape_mutant_results_keeps_done():
    from orchestration import _shape_result_for_chat
    from messages import get as _msg
    txt = _shape_result_for_chat({"ok": True, "results": [{"ok": True}]})
    assert txt == _msg("MSG_ACTION_DONE")


def test_shape_hint_wins_over_entries():
    from orchestration import _shape_result_for_chat
    txt = _shape_result_for_chat(
        {"ok": True, "entries": [["x"]], "final_message_hint": "HINT"})
    assert txt == "HINT"
