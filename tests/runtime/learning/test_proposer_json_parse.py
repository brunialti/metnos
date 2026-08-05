"""test_proposer_json_parse.py — parser JSON del proposer (B4/B5, 9/6/2026).

Copre l'estrazione a oggetti `{...}` BILANCIATI (`_strip_think` +
`_iter_balanced_json_objects` + `_parse_framework_json` in engine/proposer.py,
`_parse_candidates` in engine/proposer_metis.py) che sostituisce le regex
fragili:
  - B4: la regex Path-2 di proposer_metis (max 1 livello di nesting) perdeva
    i framework annidati steps→step→args; il greedy `\\{[\\s\\S]*\\}` di
    proposer.py inglobava prosa/oggetti multipli.
  - B5: un `<think>` lasciato aperto dal troncamento a max_tokens (o dalla
    chiusura omessa dal modello) non veniva strippato → il parser falliva
    anche con un framework valido dopo il think.

Pure-function tests: no LLM, no servizi.
"""
from __future__ import annotations

import os
import sys


from engine.proposer import (
    _iter_balanced_json_objects,
    _looks_like_truncated_framework,
    _parse_framework_json,
    _proposer_max_tokens,
    _strip_think,
)
from engine.proposer_metis import _parse_candidates

FRAMEWORK = ('{"steps": [{"tool": "find_files", '
             '"args": {"query": "fattura"}}], "final_message": "ok"}')


# ── _parse_framework_json (SimpleProposer) ─────────────────────────────────

def test_framework_pulito():
    parsed = _parse_framework_json(FRAMEWORK)
    assert parsed is not None
    assert parsed["steps"][0]["tool"] == "find_files"


def test_framework_dopo_think_non_chiuso():
    # B5: il troncamento a max_tokens (o la chiusura omessa) lascia <think>
    # aperto; il framework emesso DOPO deve essere comunque recuperato.
    raw = "<think>\nDevo cercare i file richiesti, uso find_files.\n" + FRAMEWORK
    parsed = _parse_framework_json(raw)
    assert parsed is not None
    assert parsed["steps"][0]["tool"] == "find_files"


def test_prosa_attorno_al_framework():
    raw = "Ecco il piano proposto:\n" + FRAMEWORK + "\nSpero sia utile."
    parsed = _parse_framework_json(raw)
    assert parsed is not None
    assert parsed["steps"][0]["tool"] == "find_files"


def test_framework_annidato_3_livelli():
    # B4: la vecchia regex a 1 livello di nesting non matchava gli args
    # annidati dentro steps (3 livelli di graffe: framework→step→args).
    raw = '{"steps":[{"tool":"x","args":{"k":1}}]}'
    parsed = _parse_framework_json(raw)
    assert parsed == {"steps": [{"tool": "x", "args": {"k": 1}}]}


def test_graffe_dentro_stringa_quotata():
    # Le graffe dentro una stringa JSON non devono alterare la profondita'.
    raw = ('{"steps": [{"tool": "write_files", "args": '
           '{"content": "se {x} vale } o { resta testo"}}]}')
    parsed = _parse_framework_json(raw)
    assert parsed is not None
    assert parsed["steps"][0]["args"]["content"] == "se {x} vale } o { resta testo"


def test_dict_non_framework_scartato():
    # Il PRIMO dict con "steps" vince su un dict non-framework in testa.
    raw = ('{"note": "preambolo senza steps"}\n'
           '{"steps": [{"tool": "get_now", "args": {}}]}')
    parsed = _parse_framework_json(raw)
    assert parsed is not None
    assert parsed["steps"][0]["tool"] == "get_now"


def test_fallback_primo_dict_senza_steps():
    # Nessun dict con "steps" → ritorna il primo dict (contratto parser).
    raw = 'prosa {"a": 1} altra prosa {"b": 2}'
    assert _parse_framework_json(raw) == {"a": 1}


def test_raw_vuoto_o_solo_think_troncato():
    assert _parse_framework_json("") is None
    assert _parse_framework_json("<think>solo reasoning, troncato senza JSON") is None


def test_truncated_framework_detection_is_narrow():
    assert _looks_like_truncated_framework(
        '{"steps":[{"tool":"find_files","args":{"base_path":"/tmp"}}')
    assert not _looks_like_truncated_framework(FRAMEWORK)
    assert not _looks_like_truncated_framework('{"note":"unfinished"')
    assert not _looks_like_truncated_framework("prosa arbitraria")


def test_output_budget_scales_with_observed_structural_complexity(monkeypatch):
    from engine.types import Intent
    for name in (
            "METNOS_PROPOSER_MAX_TOKENS_FAST",
            "METNOS_PROPOSER_MAX_TOKENS_COMPOUND",
            "METNOS_PROPOSER_MAX_TOKENS_COMPLEX"):
        monkeypatch.delenv(name, raising=False)
    mono = Intent(verb="read", object="files")
    moderate = Intent(verb="read", object="files", actions=[
        {"verb": "read", "object": "files"},
        {"verb": "write", "object": "files"},
    ])
    complex_intent = Intent(verb="read", object="files", actions=[
        {"verb": "read", "object": "files"},
        {"verb": "read", "object": "messages"},
        {"verb": "extract", "object": "entries"},
        {"verb": "write", "object": "files"},
    ])
    assert _proposer_max_tokens(
        query="q", intent=mono, effective_pool=["read_files"],
        use_fast=True) == 1024
    assert _proposer_max_tokens(
        query="q", intent=moderate, effective_pool=["read_files"],
        use_fast=True) == 1536
    assert _proposer_max_tokens(
        query="q", intent=complex_intent, effective_pool=["read_files"],
        use_fast=True) == 2048
    incomplete = Intent(verb="", object="", actions=[])
    assert _proposer_max_tokens(
        query="q" * 800, intent=incomplete, effective_pool=["read_files"],
        use_fast=True) == 2048
    assert _proposer_max_tokens(
        query="short", intent=incomplete,
        effective_pool=[f"tool_{index}" for index in range(32)],
        use_fast=True) == 2048


def test_simple_proposer_retries_only_truncated_framework_with_larger_budget(
        monkeypatch):
    from engine.proposer import SimpleProposer
    from engine.types import Intent

    monkeypatch.setenv("METNOS_PROPOSER_GRAMMAR", "0")
    monkeypatch.setenv("METNOS_PROPOSER_MAX_TOKENS_RETRY", "4096")
    calls = []

    def fake_llm(_system, _user, **kwargs):
        calls.append(kwargs["max_tokens"])
        if len(calls) == 1:
            return '{"steps":[{"tool":"find_files","args":{"base_path":"/tmp"}}'
        return FRAMEWORK

    intent = Intent(verb="read", object="files", actions=[
        {"verb": "read", "object": "files"},
        {"verb": "read", "object": "messages"},
        {"verb": "extract", "object": "entries"},
        {"verb": "write", "object": "files"},
    ])
    framework = SimpleProposer(
        prompt_loader=lambda *_args, **_kwargs: "prompt").propose(
            query="complex query", intent=intent,
            pool=["find_files"], excluded_hashes=set(),
            llm_call=fake_llm, catalog=None)

    assert calls == [2048, 4096]
    assert framework is not None
    assert framework.steps[0].tool == "find_files"


def test_think_chiuso_con_bozza_ignorata():
    # La bozza dentro un <think> CHIUSO viene strippata: non deve vincere
    # sul framework reale emesso dopo.
    raw = ('<think>bozza: {"steps": [{"tool": "sbagliato", "args": {}}]}</think>\n'
           '{"steps": [{"tool": "giusto", "args": {}}]}')
    parsed = _parse_framework_json(raw)
    assert parsed is not None
    assert parsed["steps"][0]["tool"] == "giusto"


# ── _parse_candidates (MetisProposer) ──────────────────────────────────────

def test_parse_candidates_array_due_framework():
    # Regressione B4: array JSON di 2 framework annidati (steps→step→args)
    # DEVE ritornare 2 candidati.
    raw = ('[{"steps": [{"tool": "find_files", "args": {"query": "a"}}]},\n'
           ' {"steps": [{"tool": "read_files", "args": {"paths": ["/tmp/a"]}}]}]')
    cands = _parse_candidates(raw)
    assert len(cands) == 2
    assert cands[0].steps[0].tool == "find_files"
    assert cands[1].steps[0].tool == "read_files"


def test_parse_candidates_oggetti_separati():
    # Oggetti multipli separati da prosa (no array): entrambi raccolti.
    raw = ('{"steps": [{"tool": "find_files", "args": {"query": "a"}}]}\n'
           'In alternativa:\n'
           '{"steps": [{"tool": "list_dirs", "args": {"path": "/tmp"}}]}')
    cands = _parse_candidates(raw)
    assert [c.steps[0].tool for c in cands] == ["find_files", "list_dirs"]


def test_parse_candidates_scarta_dict_senza_steps():
    raw = ('{"commento": "non sono un framework"}\n'
           '{"steps": [{"tool": "get_now", "args": {}}]}')
    cands = _parse_candidates(raw)
    assert [c.steps[0].tool for c in cands] == ["get_now"]


# ── primitive (_strip_think / _iter_balanced_json_objects) ────────────────

def test_strip_think_chiusi_e_aperto():
    assert _strip_think("<think>a</think>X") == "X"
    # Think aperto (troncato): il tag cade, il testo resta prosa inerte.
    assert "<think>" not in _strip_think("<think>tronc")
    assert _strip_think("") == ""


def test_iter_balanced_ignora_oggetto_troncato():
    # Oggetto a meta' (troncamento): mai emesso; il precedente completo si'.
    raw = '{"a": 1} {"steps": [{"tool": "x"'
    assert list(_iter_balanced_json_objects(raw)) == ['{"a": 1}']
