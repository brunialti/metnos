"""Render finale DETERMINISTICO sui turni a 0 entries (14/6).

Flag installer-E2E «describe 0-entries piping metis»: su un turno genuinamente
a 0 risultati il template finale rende «degenere» (summary vuoto) → l'engine
spendeva una call LLM `fast` (`_synthesize_final_from_steps`) solo per dire
«niente trovato». Fix §7.9/§2.8: `_deterministic_zero_result` corto-circuita
con il messaggio i18n `MSG_NO_RESULTS` PRIMA della synth, lasciando la synth ai
degeneri NON-vuoti (es. get_now scalare).

Test deterministici: nessun LLM, solo i due helper puri.
"""
from __future__ import annotations

import sys
from pathlib import Path


import engine.executor as EX  # noqa: E402
from engine.types import StepRun  # noqa: E402


def _step(tool: str, result: dict) -> StepRun:
    return StepRun(step_idx=1, tool=tool, args={}, result=result,
                   ok=True, latency_ms=0)


def test_zero_entries_read_then_describe():
    # read_messages 0 mail → describe item_count=0 → 0-entries genuino
    steps = [_step("read_messages", {"ok": True, "entries": []}),
             _step("describe_entries", {"ok": True, "summary": "", "item_count": 0})]
    assert EX._turn_is_zero_entries(steps) is True
    msg = EX._deterministic_zero_result(steps)
    assert msg and msg.strip(), "messaggio 0-entries vuoto"


def test_nonempty_is_not_zero():
    # 3 mail descritte → NON 0-entries → "" (la synth/render normale procede)
    steps = [_step("read_messages", {"ok": True, "entries": [{"id": 1}, {"id": 2}, {"id": 3}]}),
             _step("describe_entries", {"ok": True, "summary": "tre mail", "item_count": 3})]
    assert EX._turn_is_zero_entries(steps) is False
    assert EX._deterministic_zero_result(steps) == ""


def test_scalar_getnow_preserves_synth():
    # get_now è degenere per ALTRO motivo (scalare, niente lista) → NON 0-entries:
    # la synth LLM deve restare il fallback (no falso «Nessun risultato»).
    steps = [_step("get_now", {"ok": True, "value": "2026-06-14T10:00:00Z"})]
    assert EX._turn_is_zero_entries(steps) is False
    assert EX._deterministic_zero_result(steps) == ""


def test_producer_only_empty_list():
    # find/list senza describe, lista-payload vuota (entries/results/lines/matches)
    for key in ("entries", "results", "lines", "matches"):
        steps = [_step("find_files", {"ok": True, key: []})]
        assert EX._turn_is_zero_entries(steps) is True, key


def test_final_answer_step_ignored():
    # uno step final_answer non deve mascherare il segnale del producer a monte
    steps = [_step("read_messages", {"ok": True, "entries": []}),
             _step("final_answer", {"ok": True})]
    assert EX._turn_is_zero_entries(steps) is True


def test_latest_list_wins():
    # producer vuoto a monte, ma l'ultimo step-lista è non-vuoto → NON 0-entries
    steps = [_step("find_files", {"ok": True, "entries": []}),
             _step("filter_entries", {"ok": True, "entries": [{"x": 1}]})]
    assert EX._turn_is_zero_entries(steps) is False
