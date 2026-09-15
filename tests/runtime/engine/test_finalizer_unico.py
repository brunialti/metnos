"""ADR 0177 T5 (CP2·M2, 5/7/2026) — Finalizer unico.

`_finalize_answer_text` è l'UNICA fonte del testo di un turno `answer`
(prima: due blocchi gemelli DIVERGENTI — step final_answer e fallback
post-loop). Strategia bloccata nel tempo:
  1. render template → 2. count-only→bullets (§2.7) →
  3. vuoto/degenere → zero-result deterministico i18n → synth LLM.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

os.environ.setdefault("METNOS_ENGINE", "v3")


def _fw(final_message=""):
    from engine.types import Framework
    return Framework(steps=[], final_message=final_message)


def _steps(*results):
    from engine.executor import StepRun
    return [StepRun(step_idx=i + 1, tool=f"t{i+1}", args={}, result=r,
                    ok=True, latency_ms=1)
            for i, r in enumerate(results)]


def _no_llm(*a, **k):
    raise AssertionError("LLM chiamato dove non deve (path deterministico)")


def _llm_fixed(text):
    def _call(*a, **k):
        return text
    return _call


# 1. Render pieno: il template del proposer vince, LLM MAI chiamato.

def test_full_render_wins_no_llm():
    from engine.executor import _finalize_answer_text
    fw = _fw("Sono le ${step1.iso}.")
    st = _steps({"ok": True, "iso": "10:30"})
    out = _finalize_answer_text(fw, st, "che ore sono", _no_llm)
    assert "10:30" in out


# 2. Count-only + entries → bullets (in ENTRAMBI i call-site, ora unico).

def test_count_only_enriched_with_bullets():
    from engine.executor import _finalize_answer_text
    fw = _fw("${step1.@count}")
    st = _steps({"ok": True, "entries": [
        {"title": "Alfa"}, {"title": "Beta"}]})
    out = _finalize_answer_text(fw, st, "q", _no_llm)
    assert "Alfa" in out and "Beta" in out


# 3a. Zero-entries → MSG_NO_RESULTS deterministico (mai LLM per dire niente).

def test_zero_entries_deterministic_i18n():
    from engine.executor import _finalize_answer_text
    from messages import get as _msg
    fw = _fw("Ecco: ${step1.entries}")
    st = _steps({"ok": True, "entries": []})
    out = _finalize_answer_text(fw, st, "q", _no_llm)
    assert out == _msg("MSG_NO_RESULTS")


# 3b. Degenere non-vuoto (placeholder perso) → synth LLM.

def test_degenerate_falls_to_synth():
    from engine.executor import _finalize_answer_text
    fw = _fw("Sono le ${step1.manca}.")
    st = _steps({"ok": True, "iso": "10:30"})
    out = _finalize_answer_text(fw, st, "che ore sono",
                                _llm_fixed("Sono le 10:30."))
    assert out == "Sono le 10:30."


# 3c. Template statico VUOTO → MAI risposta muta: o il fallback del render
#     (scalare dell'ultima observation) o la synth. Contratto: non-vuoto.

def test_empty_static_template_not_mute():
    from engine.executor import _finalize_answer_text
    fw = _fw("")
    st = _steps({"ok": True, "value": 42})
    out = _finalize_answer_text(fw, st, "q", _llm_fixed("Il valore è 42."))
    assert out.strip() != ""
    assert "42" in out


# 4. Statico legittimo non-vuoto resta intatto (nessuna synth spuria).

def test_static_text_kept():
    from engine.executor import _finalize_answer_text
    fw = _fw("Operazione pianificata.")
    st = _steps({"ok": True})
    out = _finalize_answer_text(fw, st, "q", _no_llm)
    assert out == "Operazione pianificata."


@pytest.mark.parametrize("template", ["Ho chiuso il programma.",
                                     "Closed ${step1.ok_count} applications."])
@pytest.mark.parametrize("outcome", ["no_effect", "reversible", "irreversible"])
def test_terminal_effect_receipt_outranks_pre_execution_claim(template, outcome):
    from engine.executor import _finalize_answer_text
    hint = "Already closed on the requested device: Example."
    st = _steps({"ok": True, "ok_count": 1, "results": [{}],
                 "_undo": {"outcome": outcome}, "final_message_hint": hint})
    assert _finalize_answer_text(_fw(template), st, "q", _no_llm) == hint


def test_effect_receipt_ignores_final_answer_pseudostep():
    from engine.executor import _finalize_answer_text
    hint = "Risulta già chiuso sul dispositivo richiesto."
    st = _steps({"ok": True, "_undo": {"outcome": "no_effect"},
                 "final_message_hint": hint}, {"ok": True})
    st[-1].tool = "final_answer"
    assert _finalize_answer_text(_fw("Eseguito."), st, "q", _no_llm) == hint


@pytest.mark.parametrize("metadata", [None, {}, {"outcome": "invalid"},
                                      {"outcome": []}])
def test_only_typed_effect_receipt_overrides_read_presentation(metadata):
    from engine.executor import _finalize_answer_text
    st = _steps({"ok": True, "value": 42, "_undo": metadata,
                 "final_message_hint": "An optional read-only summary."})
    assert _finalize_answer_text(
        _fw("Value: ${step1.value}."), st, "q", _no_llm) == "Value: 42."


@pytest.mark.parametrize("hint", [None, "", "  ", "<missing:MSG_RECEIPT>"])
def test_effect_receipt_without_usable_hint_preserves_render(hint):
    from engine.executor import _finalize_answer_text
    st = _steps({"ok": True, "_undo": {"outcome": "no_effect"},
                 "final_message_hint": hint})
    assert _finalize_answer_text(
        _fw("Nothing changed."), st, "q", _no_llm) == "Nothing changed."


def test_prior_effect_receipt_does_not_replace_terminal_read_result():
    from engine.executor import _finalize_answer_text
    st = _steps({"ok": True, "_undo": {"outcome": "reversible"},
                 "final_message_hint": "Earlier action completed."},
                {"ok": True, "value": 42})
    assert _finalize_answer_text(
        _fw("Value: ${step2.value}."), st, "q", _no_llm) == "Value: 42."


# 5. Contratto call-site: nessun blocco gemello residuo — la logica
#    render/degenere/synth vive SOLO nel Finalizer.

def test_single_source_no_twin_blocks():
    src = (Path(_RUNTIME) / "engine" / "executor.py").read_text()
    run_body = src.split("def _finalize_answer_text", 1)[1]
    assert run_body.count("_render_is_degenerate(") == 1, (
        "logica degenere duplicata fuori dal Finalizer")
    assert run_body.count("_finalize_answer_text(") == 2, (
        "attesi ESATTAMENTE 2 call-site (terminator + fallback post-loop)")


@pytest.mark.parametrize("lang", ["it", "en"])
def test_empty_table_explains_selection_while_explicit_count_remains_numeric(lang, monkeypatch):
    import i18n
    from engine.executor import _finalize_answer_text, _render_final_message
    from messages import get as msg
    monkeypatch.setattr(i18n, "current_lang", lambda: lang)
    steps = _steps({"ok": True, "entries": [],
                    "metadata": {"count_in": 14, "count_out": 0, "dropped": 14}})
    steps[0].tool = "filter_entries"
    expected = msg("MSG_PROCESSOR_EMPTY", tool="filter_entries")
    assert _finalize_answer_text(_fw("${step1.@table}"), steps, "q", _no_llm) == expected
    assert _render_final_message("${step1.@count}", steps) == "0"
    assert _finalize_answer_text(_fw("${step1.@count}"), steps, "q", _no_llm) == "0"
    assert _finalize_answer_text(_fw(msg("MSG_COUNT_TOTAL", count="${step1.@count}")), steps, "q", _no_llm) == msg("MSG_COUNT_TOTAL", count=0)


def test_empty_table_uses_its_referenced_step_not_an_unrelated_later_result():
    from engine.executor import _render_final_message
    from messages import get as msg
    steps = _steps({"ok": True, "entries": []}, {"ok": True, "entries": [{"name": "Later"}]})
    assert _render_final_message("${step1.@table}", steps) == msg("MSG_NO_RESULTS")
    assert _render_final_message("${steps.0.@table}", steps) == msg("MSG_NO_RESULTS")
    assert "Later" in _render_final_message("${step2.@table}", steps)


def test_empty_table_preserves_source_note_and_count_only_totals():
    from engine.executor import _render_final_message
    from messages import get as msg
    steps = _steps({"ok": True, "entries": [], "message": "Bounded source scope."})
    assert _render_final_message("${step1.@table}", steps) == msg("MSG_NO_RESULTS") + "\n\nBounded source scope."
    steps[0].result["available_total"] = 14
    assert _render_final_message("${step1.@table}", steps).startswith("14")


@pytest.mark.parametrize("lang", ["it", "en"])
@pytest.mark.parametrize("kind", ["messages", "files"])
def test_simulated_read_classify_filter_pipeline_has_honest_empty_final(lang, kind, monkeypatch):
    """Actual policy, piping, filter, finalizer and persistence; no model calls."""
    import importlib.util
    import i18n
    from types import SimpleNamespace
    from agent_runtime import StepLog, TurnLog
    from engine.executor import Executor
    from engine.types import Framework, Intent, StepSpec
    from messages import get as msg
    from output_policy import normalize_terminal

    monkeypatch.setattr(i18n, "current_lang", lambda: lang)
    path = _RUNTIME.parent / "executors/filter_entries/filter_entries.py"
    spec = importlib.util.spec_from_file_location("_empty_final_filter_fixture", path)
    filtering = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(filtering)
    source_tool = "read_messages" if kind == "messages" else "find_files"
    rows = [{"id": n, "name": f"Item {n}"} for n in range(14)]
    calls = []

    def invoke(tool, args):
        calls.append(tool)
        if tool == source_tool:
            return {"ok": True, "entries": rows}
        if tool == "classify_entries":
            return {"ok": True, "entries": [{**row, "importance": "low"} for row in args["entries"]]}
        assert tool == "filter_entries"
        return filtering.invoke(args)

    schema = {"type": "object", "properties": {"entries": {"type": "array", "items": {"type": "object"}}}}
    catalog = [SimpleNamespace(name=tool, args_schema=schema, presentation={"default_view": "list"})
               for tool in (source_tool, "classify_entries", "filter_entries")]
    framework = Framework(steps=[
        StepSpec(source_tool, {}),
        StepSpec("classify_entries", {"from_step": 1}),
        StepSpec("filter_entries", {"from_step": 2, "where_field": "importance", "where_value": "important"}),
        StepSpec("final_answer", {}),
    ])
    framework, _ = normalize_terminal(framework, Intent(verb="find", object=kind), "q", catalog=catalog)
    assert framework.final_message == "${step3.@table}"
    run = Executor(invoke_executor=invoke, llm_call_fast=_no_llm, catalog=catalog).run(framework, query="q")
    assert calls == [source_tool, "classify_entries", "filter_entries"]
    assert all(step.ok for step in run.steps)
    assert run.final_kind == "answer"
    expected = msg("MSG_PROCESSOR_EMPTY", tool="filter_entries")
    assert run.final_text == expected
    log = TurnLog(ts_start=0.0, user_query="q", channel="test", actor="empty-final-fixture")
    for step in run.steps:
        saved = StepLog(step_num=step.step_idx, chosen_tool=step.tool)
        saved.result = step.result
        log.steps.append(saved)
    log.final_kind = "answer"
    log.final_message = run.final_text
    log.write()
    assert log.effect_counts["items"] == 28
    assert log.effect_counts["mutations"] == 0
    assert log.final_message == expected
