"""Meta-oggetto `entries` nell'intent (10/7, turn 6eb54e06).

`entries` esiste SOLO in-pipeline (from_step, §2.2): da una query utente è un
leak del meta-vocabolario nel classificatore. Live: «quali sono gli album che
ho su google foto» → LLM object=entries conf=1.00 → `align_objects` riscriveva
il piano su `find_entries` AZZERANDO gli args → invalid_args («manca 'store'»).

Regola: demote a None per ogni verbo NON-transformer; i transformer
(`describe/filter/sort/classify/extract/compute`, derivati dai `*_entries`
di `tool_grammar._UNIVERSAL_HELPERS`) lo tengono (consumo legittimo di output
in-memory). Deterministico §7.9, zero liste a mano §7.3.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

import intent_extractor as IE  # noqa: E402


def _llm(payload: dict):
    """Fake llm_call: ritorna il JSON dato qualunque sia il prompt."""
    def call(prompt, query, max_tokens=None, think=None):
        return json.dumps(payload)
    return call


def test_entries_demoted_for_producer_verb():
    out = IE.extract_intent("quali sono gli album che ho su google foto",
                            _llm({"verb": "list", "object": "entries"}))
    assert out is not None
    assert out.get("verb") == "list"
    assert out.get("object") is None          # demoted: mai entries da query
    for a in out.get("actions") or []:
        assert a.get("object") != "entries"


def test_entries_kept_for_transformer_verb():
    out = IE.extract_intent("filtra i risultati per data",
                            _llm({"verb": "filter", "object": "entries"}))
    assert out is not None
    assert out.get("verb") == "filter"
    assert out.get("object") == "entries"     # transformer: consumo legittimo


def test_entries_with_unknown_verb_yields_fallback():
    """Verbo fuori vocab + entries demotato = NIENTE da estrarre → None
    (contratto: il caller ricade sul lexicon ranking, come il bypass undo)."""
    out = IE.extract_intent("gli album di google foto",
                            _llm({"verb": "boh", "object": "entries"}))
    assert out is None


def test_transformer_set_derived_from_universal_helpers():
    from tool_grammar import _UNIVERSAL_HELPERS
    expected = {n.rsplit("_", 1)[0] for n in _UNIVERSAL_HELPERS
                if n.endswith("_entries")}
    assert IE._ENTRIES_INTENT_VERBS == frozenset(expected)
    assert "find" not in IE._ENTRIES_INTENT_VERBS
    assert "list" not in IE._ENTRIES_INTENT_VERBS


def test_intent_prompt_uses_instance_language(monkeypatch):
    """Il prompt e i confini devono usare la stessa lingua d'istanza."""
    import i18n

    observed = {}

    def fake_prompt(role, lang, **variables):
        observed.update(role=role, lang=lang,
                        boundaries=variables.get("boundaries_block"))
        return "prompt"

    monkeypatch.setattr(IE.prompt_loader, "get", fake_prompt)
    monkeypatch.setattr(IE, "_vocab_boundaries",
                        lambda lang: f"boundaries:{lang}")
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "fr")

    out = IE.extract_intent(
        "classe ces éléments",
        _llm({"verb": "classify", "object": "entries"}),
    )

    assert out is not None
    assert observed == {
        "role": "intent_extractor_v4",
        "lang": "fr",
        "boundaries": "boundaries:fr",
    }


@pytest.mark.parametrize("lang,prefix", [
    ("it", "cerca foto di un computer nella cartella "),
    ("en", "find photos of a computer in folder "),
])
@pytest.mark.parametrize("path", [
    "/var/lib/metnos-service/.local/share/metnos/workspace/live-check/Immagini",
    "/archive/server/cpu/photos",
    r"C:\archive\server\ram\photos",
    r"\\server\disk\photos",
])
def test_path_markers_do_not_bypass_semantic_intent(monkeypatch, lang, prefix, path):
    """Identifier words cannot turn a content search into a health query."""
    import i18n

    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", lang)
    query = prefix + path
    seen = []

    def call(prompt, original, **kwargs):
        seen.append(original)
        return json.dumps({"kind": "action", "verb": "find", "object": "images"})

    result = IE.extract_intent(query, call)
    assert seen == [query]
    assert (result["verb"], result["object"]) == ("find", "images")


@pytest.mark.parametrize("lang,query", [
    ("it", "qual è la cpu del server"),
    ("it", "stato del server"),
    ("en", "what is the server cpu"),
    ("en", "server status"),
])
def test_real_health_query_keeps_deterministic_intent(monkeypatch, lang, query):
    import i18n

    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", lang)

    def forbidden_call(*args, **kwargs):
        pytest.fail("A pure hardware question should retain its fast path")

    result = IE.extract_intent(query, forbidden_call)
    assert (result["verb"], result["object"]) == ("get", "processes")


@pytest.mark.parametrize("query", [
    "cerca foto di un computer in /archive/server/cpu/photos",
    "find photos of a computer in C:\\archive\\server\\ram\\photos",
])
def test_health_consumers_ignore_path_markers(query):
    from engine.dispatch import _ensure_health_arg
    from engine.types import Framework, StepSpec
    from target_device import _has_machine_focus

    assert not _has_machine_focus(query)
    args = {"top": 5}
    framework = Framework(steps=[StepSpec("get_processes", dict(args))])
    out = _ensure_health_arg(framework, query, [])
    assert out.steps[0].args == args
