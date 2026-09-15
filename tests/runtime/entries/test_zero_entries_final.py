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

import pytest

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


@pytest.mark.parametrize("lang", ["it", "en"])
def test_empty_selection_reuses_localized_processor_explanation(lang, monkeypatch):
    import i18n
    from messages import get as msg
    monkeypatch.setattr(i18n, "current_lang", lambda: lang)
    steps = [
        _step("read_objects", {"ok": True, "entries": [{"id": n} for n in range(14)]}),
        _step("filter_entries", {"ok": True, "entries": [],
                                "metadata": {"count_in": 14, "count_out": 0, "dropped": 14}}),
    ]
    assert EX._turn_is_zero_entries(steps)
    assert EX._deterministic_zero_result(steps) == msg("MSG_PROCESSOR_EMPTY", tool="filter_entries")


def test_empty_source_is_not_described_as_a_nonempty_selection():
    from messages import get as msg
    steps = [
        _step("read_objects", {"ok": True, "entries": []}),
        _step("filter_entries", {"ok": True, "entries": [],
                                "metadata": {"count_in": 0, "count_out": 0, "dropped": 0}}),
    ]
    assert EX._deterministic_zero_result(steps) == msg("MSG_NO_RESULTS")


@pytest.mark.parametrize("tool,result", [
    ("filter_entries", {"ok": False, "entries": [], "error": "invalid predicate"}),
    ("get_now", {"ok": True, "value": "12:00"}),
    ("write_objects", {"ok": True, "ok_count": 2, "results": [{}, {}]}),
    ("write_objects", {"ok": False, "ok_count": 1, "results": [{}]}),
])
def test_scalar_failure_or_mutation_is_not_an_empty_collection(tool, result):
    steps = [_step("read_objects", {"ok": True, "entries": []}), _step(tool, result)]
    assert EX._turn_is_zero_entries(steps) is False
    assert EX._deterministic_zero_result(steps) == ""


@pytest.mark.parametrize("counter", ["available_total", "count", "ok_count", "item_count"])
def test_count_only_without_materialized_rows_is_still_a_result(counter):
    steps = [_step("find_objects", {"ok": True, "entries": [], counter: 14})]
    assert EX._turn_is_zero_entries(steps) is False


def test_terminal_count_supports_engine_and_persisted_log_shapes_without_summing():
    import json
    from pipeline_effects import pipeline_effect_counts, terminal_collection_output
    steps = [
        {"tool": "read_objects", "result": {"ok": True, "entries": [{}, {}]}},
        {"chosen_tool": "classify_entries", "result": json.dumps({"ok": True, "entries": [{}, {}]})},
        {"chosen_tool": "filter_entries", "result": {"ok": True, "entries": []}},
        {"tool": "final_answer", "result": {"ok": True}},
    ]
    assert terminal_collection_output(steps)[2] == 0
    # Work/effect telemetry remains unchanged; it is not a final result count.
    assert pipeline_effect_counts(steps)["items"] == 4


def test_terminal_output_count_uses_materialized_rows_not_precap_availability():
    from pipeline_effects import terminal_collection_output
    step = _step("find_objects", {"ok": True, "entries": [{}, {}], "available_total": 100})
    assert terminal_collection_output([step])[2] == 2
