"""MetisTerminator: i risultati parziali presentati non devono MAI contenere
HTML grezzo (bug "azione schedulata invia messaggio errato", caso reale rocm).

`_best_entries` preferisce lo step piu' a valle (read_urls_html con body): se
quel body e' markup grezzo (SPA non parsata), il terminator lo sanifica
(output_format._strip_html_to_text) prima di renderlo. Deterministico §7.9.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

from engine.terminator_metis import MetisTerminator
from engine.types import Intent, RunResult


class _Step:
    def __init__(self, result, tool=""):
        self.result = result
        self.tool = tool


def _run(entries):
    return RunResult(steps=[_Step({"ok": True, "entries": entries})])


def _intent():
    return Intent(verb="find", object="urls", keywords=["rocm"])


def test_raw_html_body_text_is_stripped():
    run = _run([{
        "url": "https://rocm.docs.amd.com/en/latest/release/versions.html",
        "title": "",
        "body_text": ('<!DOCTYPE html> <html data-content_root="../" lang="en">'
                      "<head><title>ROCm 6.2.0</title></head><body>x</body></html>"),
    }])
    resp = MetisTerminator().explain(
        query="versione AMD ROCm", intent=_intent(), failed_run=run)
    assert "<!DOCTYPE" not in resp.final_text
    assert "<html" not in resp.final_text
    assert "ROCm 6.2.0" in resp.final_text           # testo utile estratto
    assert "rocm.docs.amd.com" in resp.final_text     # url presente


def test_clean_snippet_preserved():
    run = _run([{
        "url": "https://x/y",
        "title": "ROCm versions",
        "snippet": "Ultima versione 6.2.0 rilasciata.",
    }])
    resp = MetisTerminator().explain(
        query="rocm", intent=_intent(), failed_run=run)
    assert "6.2.0 rilasciata" in resp.final_text


def test_no_entries_falls_back_without_crash():
    run = RunResult(steps=[])
    resp = MetisTerminator().explain(
        query="x", intent=_intent(), failed_run=run)
    assert isinstance(resp.final_text, str) and resp.final_text


def test_failed_site_entry_is_not_presented_as_partial_search_result():
    run = RunResult(steps=[_Step({
        "ok": False, "error_class": "quota_exceeded", "entries": [{
            "url": "https://telepass.com", "ok": False,
            "session_id": None, "reason_code": "quota_exceeded",
        }],
    })])
    assert MetisTerminator._best_entries(run) == []
    resp = MetisTerminator().explain(
        query="accedi al sito", intent=_intent(), failed_run=run,
        error_class="quota_exceeded")
    assert resp.root_cause != "partial_results"
    assert "risultati pertinenti" not in resp.final_text.lower()


def test_mixed_partial_entries_keep_only_successful_items():
    run = RunResult(steps=[_Step({"ok": False, "entries": [
        {"url": "https://failed.test", "ok": False},
        {"url": "https://useful.test", "ok": True, "title": "Useful"},
    ]})])
    assert MetisTerminator._best_entries(run) == [{
        "url": "https://useful.test", "ok": True, "title": "Useful"}]


def test_open_sites_seed_is_not_a_partial_user_result():
    run = RunResult(steps=[
        _Step({"ok": True, "entries": [{
            "url": "https://portal.test/", "ok": True,
            "title": "Portal home",
        }]}, tool="open_sites"),
        _Step({"ok": False, "error_class": "navigation_failed"},
              tool="act_sites"),
    ])
    assert MetisTerminator._best_entries(run) == []
    resp = MetisTerminator().explain(
        query="mostra le prenotazioni", intent=_intent(), failed_run=run,
        error_class="navigation_failed")
    assert resp.root_cause != "partial_results"
    assert "risultati pertinenti" not in resp.final_text.lower()
