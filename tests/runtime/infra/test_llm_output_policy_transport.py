"""Il contratto d'uscita vale su OGNI trasporto del gateway LLM.

`call_llm` ha due strade verso lo stesso tier: il processo monouso
byte-deterministico (§11) e il server HTTP condiviso. `output_policy="public"`
è una garanzia dichiarata dal chiamante — il Tutor la usa per rifiutare i
marker strutturali interni — quindi deve valere su entrambe: una garanzia
applicata su una sola strada è un falso successo (§2.8).

Difetto trovato il 25/7/2026: il ramo deterministico ritornava il testo
grezzo del processo senza passare da `_postprocess_response`. Nessun consumer
lo esercitava ancora (describe usa la policy `raw`), ma la combinazione
`deterministic=True` + `public` — quella proposta per il composer del Tutor —
avrebbe scavalcato il filtro in silenzio.
"""

from __future__ import annotations

import pytest

import llm_helpers


_LLAMACPP_TIER = {
    "provider": "llamacpp", "model": "local",
    "temperature": 0.0, "think": False, "reasoning_budget": 0,
}


@pytest.fixture
def deterministic_tier(monkeypatch):
    """Tier che soddisfa le precondizioni del path deterministico."""

    monkeypatch.setattr(llm_helpers, "resolved_tier_spec",
                        lambda _tier: dict(_LLAMACPP_TIER))
    monkeypatch.setattr(llm_helpers, "_tier_endpoint",
                        lambda _tier: "http://127.0.0.1:9/")
    monkeypatch.setenv("METNOS_LLM_SEED", "42")


def _proc_returning(text: str):
    def _fake(_prompt, _payload, **kwargs):
        kwargs.get("meta_out", {})["prompt_sha"] = "sha256:test"
        return text
    return _fake


def test_public_policy_rejects_internal_marker_on_the_deterministic_path(
        monkeypatch, deterministic_tier):
    monkeypatch.setattr(llm_helpers, "_call_llm_proc",
                        _proc_returning("<think>ragionamento</think> risposta"))
    with pytest.raises(ValueError, match="internal marker"):
        llm_helpers.call_llm({"q": "x"}, "PROMPT", tier="wise",
                             deterministic=True, output_policy="public")


def test_deterministic_path_normalizes_and_reports_its_identity(
        monkeypatch, deterministic_tier):
    monkeypatch.setattr(llm_helpers, "_call_llm_proc",
                        _proc_returning("  risposta pulita  "))
    text, meta = llm_helpers.call_llm({"q": "x"}, "PROMPT", tier="wise",
                                      deterministic=True,
                                      output_policy="public")
    assert text == "risposta pulita"
    assert meta["deterministic"] is True
    assert meta["prompt_sha"] == "sha256:test"


def test_raw_policy_keeps_marker_text_on_the_deterministic_path(
        monkeypatch, deterministic_tier):
    """`raw` non filtra: e' il contratto usato da describe, non va inasprito."""

    monkeypatch.setattr(llm_helpers, "_call_llm_proc",
                        _proc_returning("<think>x</think> corpo"))
    text, _meta = llm_helpers.call_llm({"q": "x"}, "PROMPT", tier="wise",
                                       deterministic=True)
    assert "<think>" in text


def test_unavailable_deterministic_path_falls_back_and_still_filters(
        monkeypatch, deterministic_tier):
    """Se il processo non e' disponibile si passa all'HTTP, che filtra."""

    monkeypatch.setattr(llm_helpers, "_call_llm_proc",
                        lambda *a, **k: None)

    class _Response:
        text = "<think>fuga</think> risposta"
        usage = {}

    class _Provider:
        def chat(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(llm_helpers, "LlamaCppProvider",
                        lambda **_kwargs: _Provider())
    with pytest.raises(ValueError, match="internal marker"):
        llm_helpers.call_llm({"q": "x"}, "PROMPT", tier="wise",
                             deterministic=True, output_policy="public")
