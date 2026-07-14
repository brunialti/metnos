"""Contratto sites: pagina autenticata -> record tipizzati -> risposta."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


def _meta(in_tokens=10, out_tokens=5, latency_ms=2):
    return {"in_tokens": in_tokens, "out_tokens": out_tokens,
            "latency_ms": latency_ms}


def test_extract_entries_infers_bounded_schema_when_fields_are_omitted(
        monkeypatch):
    import extract_entries as module

    responses = iter([
        ('{"fields":["nome_articolo","prezzo","quantita"]}', _meta()),
        ('[{"nome_articolo":"Lampada da tavolo","prezzo":"19.90",'
         '"quantita":"2"}]', _meta()),
    ])
    calls = []

    def fake_call(query, prompt, **kwargs):
        calls.append((query, prompt, kwargs))
        return next(responses)

    monkeypatch.setattr(module, "call_llm", fake_call)
    out = module.handle_extract_entries({
        "entries": [{"text": (
            "Carrello\nLampada da tavolo\nEUR 19,90\nQuantita 2\n"
            "Contenuto sponsorizzato\nAiuto e condizioni") }],
        "instruction": "dimmi quali sono gli articoli salvati",
        "drill_down": False,
    })

    assert out["ok"] is True
    assert out["fields_inferred"] is True
    assert out["fields"] == ["nome_articolo", "prezzo", "quantita"]
    assert out["entries"] == [{
        "nome_articolo": "Lampada da tavolo",
        "prezzo": "19.90", "quantita": "2",
    }]
    assert len(calls) == 2
    assert "Lampada da tavolo" in calls[0][0]
    assert calls[0][2]["max_tokens"] == 256


def test_extract_entries_rejects_malformed_explicit_fields_without_inference(
        monkeypatch):
    import extract_entries as module

    monkeypatch.setattr(module, "call_llm", lambda *_a, **_k: (
        (_ for _ in ()).throw(AssertionError("LLM must not run"))))
    out = module.handle_extract_entries({
        "entries": ["row"], "fields": ["valid", 7],
    })
    assert out["ok"] is False
    assert out["error_class"] == "invalid_args"


def test_extract_entries_tool_contract_marks_only_source_as_required():
    from extract_entries import EXTRACT_ENTRIES_TOOL

    schema = EXTRACT_ENTRIES_TOOL["function"]["parameters"]
    assert schema["required"] == ["from_step"]
    assert "fields" in schema["properties"]


def _structured_framework():
    from engine.types import Framework, StepSpec

    return Framework(steps=[
        StepSpec(tool="open_sites", args={
            "urls": ["https://shop.example"]}),
        StepSpec(tool="login_sites", args={"from_step": 1}),
        StepSpec(tool="act_sites", args={
            "from_step": 2, "action": "cerca gli elementi salvati"}),
        StepSpec(tool="read_sites", args={"from_step": 3}),
        StepSpec(tool="describe_entries", args={"from_step": 4}),
        StepSpec(tool="final_answer", args={}),
    ], final_message="${step5.summary}")


def _structured_intent():
    from engine.types import Intent

    return Intent(verb="open", object="sites", actions=[
        {"verb": "open", "object": "sites"},
        {"verb": "login", "object": "sites"},
        {"verb": "act", "object": "sites"},
        {"verb": "read", "object": "sites"},
    ])


def test_sites_guard_inserts_extract_between_read_and_describe_idempotently():
    from engine.dispatch import _ensure_site_session_precursor

    query = ("entra nel sito shop.example e dimmi quali sono "
             "gli elementi salvati")
    out = _ensure_site_session_precursor(
        _structured_framework(), _structured_intent(), query, None)

    assert [step.tool for step in out.steps] == [
        "open_sites", "login_sites", "act_sites", "read_sites",
        "extract_entries", "describe_entries", "final_answer",
    ]
    assert out.steps[4].args == {
        "from_step": 4, "instruction": query, "drill_down": False}
    assert out.steps[5].args == {
        "from_step": 5, "style": "by_relevance", "context": query,
        "data_kind": "entries",
    }
    assert out.final_message == "${step6.summary}"

    again = _ensure_site_session_precursor(
        out, _structured_intent(), query, None)
    assert [(step.tool, step.args) for step in again.steps] == [
        (step.tool, step.args) for step in out.steps]
    assert again.final_message == out.final_message


def test_sites_guard_recruits_read_and_extract_from_semantic_request():
    from engine.dispatch import _ensure_site_session_precursor
    from engine.types import Framework, Intent, StepSpec

    query = "entra nel sito shop.example e mostrami gli elementi disponibili"
    out = _ensure_site_session_precursor(
        Framework(steps=[StepSpec(tool="open_sites", args={
            "urls": ["https://shop.example"]})]),
        Intent(verb="open", object="sites", actions=[
            {"verb": "open", "object": "sites"}]),
        query, None)
    assert [step.tool for step in out.steps] == [
        "open_sites", "login_sites", "act_sites", "read_sites",
        "extract_entries", "describe_entries",
    ]
    assert out.steps[2].args == {
        "from_step": 2, "action": query, "_goal_mode": True}
    assert out.steps[3].args["from_step"] == 3


def test_canonical_live_query_gets_goal_before_typed_read():
    from engine.dispatch import _ensure_site_session_precursor
    from engine.types import Framework, Intent, StepSpec

    query = ("entra nel sito amazon e dimmi quali sono gli articoli "
             "nel carrello")
    out = _ensure_site_session_precursor(
        Framework(steps=[StepSpec(tool="open_sites", args={
            "urls": ["https://www.amazon.it"]})]),
        Intent(verb="open", object="sites", actions=[
            {"verb": "open", "object": "sites"},
            {"verb": "read", "object": None},
        ]),
        query, None)

    assert [step.tool for step in out.steps] == [
        "open_sites", "login_sites", "act_sites", "read_sites",
        "extract_entries", "describe_entries",
    ]
    assert out.steps[2].args == {
        "from_step": 2, "action": query, "_goal_mode": True}
    assert out.steps[3].args["from_step"] == 3
    assert out.steps[4].args["from_step"] == 4
    assert out.steps[5].args["from_step"] == 5


def test_public_structured_site_reads_after_action_and_is_idempotent():
    from engine.dispatch import _ensure_site_session_precursor
    from engine.types import Framework, Intent, StepSpec

    query = "apri shop.example e dimmi quali sono gli elementi disponibili"
    framework = Framework(steps=[
        StepSpec(tool="open_sites", args={
            "urls": ["https://shop.example"]}),
        StepSpec(tool="read_sites", args={"from_step": 1}),
        StepSpec(tool="act_sites", args={
            "from_step": 2, "action": "clicca catalogo"}),
    ])
    intent = Intent(verb="open", object="sites", actions=[
        {"verb": "open", "object": "sites"},
        {"verb": "act", "object": "sites"},
        {"verb": "read", "object": "sites"},
    ])

    out = _ensure_site_session_precursor(framework, intent, query, None)
    assert [step.tool for step in out.steps] == [
        "open_sites", "act_sites", "read_sites", "extract_entries",
        "describe_entries",
    ]
    assert out.steps[1].args["from_step"] == 1
    assert out.steps[2].args["from_step"] == 2
    assert out.steps[3].args["from_step"] == 3
    again = _ensure_site_session_precursor(out, intent, query, None)
    assert [(step.tool, step.args) for step in again.steps] == [
        (step.tool, step.args) for step in out.steps]


def test_synthetic_sites_chain_returns_structured_items(monkeypatch):
    import extract_entries as extract_module
    from engine.dispatch import _ensure_site_session_precursor
    from engine.executor import Executor

    responses = iter([
        ('{"fields":["label","amount","count"]}', _meta()),
        ('[{"label":"Item A","amount":"10.00","count":"1"},'
         '{"label":"Item B","amount":"8.50","count":"2"}]', _meta()),
    ])
    monkeypatch.setattr(
        extract_module, "call_llm",
        lambda *_a, **_k: next(responses))

    query = ("entra nel sito shop.example e dimmi quali sono "
             "gli elementi salvati")
    framework = _ensure_site_session_precursor(
        _structured_framework(), _structured_intent(), query, None)

    def invoke(tool, args):
        if tool in {"open_sites", "login_sites"}:
            return {"ok": True, "entries": [{"session_id": "sid"}]}
        if tool == "act_sites":
            return {"ok": True, "results": [{
                "session_id": "sid", "ok": True, "executed": True}]}
        if tool == "read_sites":
            return {"ok": True, "entries": [{
                "session_id": "sid", "url": "https://shop.example/saved",
                "text": "Saved\nItem A EUR 10 x1\nItem B EUR 8.50 x2",
            }]}
        if tool == "extract_entries":
            return extract_module.handle_extract_entries(args)
        if tool == "describe_entries":
            rows = args["entries"]
            return {"ok": True, "summary": "\n".join(
                f"- {row['label']}: {row['amount']} x{row['count']}"
                for row in rows)}
        raise AssertionError(tool)

    run = Executor(invoke_executor=invoke).run(framework, query=query)
    assert run.final_kind == "answer"
    assert "- Item A: 10.00 x1" in run.final_text
    assert "- Item B: 8.50 x2" in run.final_text
    assert [step.tool for step in run.steps] == [
        "open_sites", "login_sites", "act_sites", "read_sites",
        "extract_entries", "describe_entries",
    ]


def test_synthetic_empty_structured_result_is_reported_honestly(monkeypatch):
    import extract_entries as extract_module
    from describe_entries import handle_describe_entries
    from engine.dispatch import _ensure_site_session_precursor
    from engine.executor import Executor
    from messages import get as message

    responses = iter([
        ('{"fields":["label","amount"]}', _meta()),
        ("[]", _meta()),
    ])
    monkeypatch.setattr(
        extract_module, "call_llm",
        lambda *_a, **_k: next(responses))
    query = ("entra nel sito shop.example e dimmi quali sono "
             "gli elementi salvati")
    framework = _ensure_site_session_precursor(
        _structured_framework(), _structured_intent(), query, None)

    def invoke(tool, args):
        if tool in {"open_sites", "login_sites"}:
            return {"ok": True, "entries": [{"session_id": "sid"}]}
        if tool == "act_sites":
            return {"ok": True, "results": [{
                "session_id": "sid", "ok": True, "executed": True}]}
        if tool == "read_sites":
            return {"ok": True, "entries": [{"text": "No saved items"}]}
        if tool == "extract_entries":
            return extract_module.handle_extract_entries(args)
        if tool == "describe_entries":
            return handle_describe_entries(args)
        raise AssertionError(tool)

    run = Executor(invoke_executor=invoke).run(framework, query=query)
    assert run.final_kind == "answer"
    assert run.final_text == message("MSG_NO_RESULTS")


def test_sites_extraction_routing_has_no_vendor_object_or_field_literals():
    from engine.dispatch import _ensure_site_session_precursor

    source = inspect.getsource(_ensure_site_session_precursor).casefold()
    for forbidden in (
            "amazon", "booking", "carrello", "nome_articolo",
            "prezzo", "quantita"):
        assert forbidden not in source
    assert "\"cart\"" not in source and "'cart'" not in source
    for linguistic_surface in (
            "dimmi quali", "quali sono", "tell me which", "elenca"):
        assert linguistic_surface not in source
    assert "_dl_match" in source
    assert '"sites.structured_record_request"' in source
