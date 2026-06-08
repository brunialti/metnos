#!/usr/bin/env python3
"""intent_extractor.py — estrae verbo+oggetto canonici da una richiesta utente.

Approccio: una chiamata LLM (gemma 4 26B fast/middle tier, think=False, ~500ms)
con prompt minimo che chiede al modello di mappare la richiesta sul vocabolario
chiuso di Metnos (20 verbi × 11 oggetti).

Pipeline:
    query → LLM → {verb, object, confidence}
                → caller usa per filtrare candidates

Vantaggi rispetto al lexicon match:
- Robusto a variazioni di linguaggio (IT/EN, conjugazioni, sinonimi, idiomi).
- Cross-language (gemma e' multilingue).
- Non richiede manutenzione di un dizionario manuale.

Latenza: ~500-800ms con gemma 4 26B think=False, num_predict=80.

Failure mode:
- LLM down → ritorna None, caller deve fall-back al lexicon.
- LLM produce JSON malformato → parser tenta recupero best-effort, altrimenti None.
- LLM produce verbo/oggetto fuori vocabolario → ritorna None (non si forza).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from vocab import (
    ACTIONS as VOCAB_VERBS,
    OBJECTS as VOCAB_OBJECTS,
    render_actions_inline as _vocab_verbs_inline,
    render_objects_inline as _vocab_objects_inline,
)
from logging_setup import get_logger
log = get_logger(__name__)

import prompt_loader
from config import DEFAULT_LANG

# Prompt persistito in `runtime/prompts/<lang>/intent_extractor.j2` (ADR 0092 Phase 2).
# Renderizzato lazy a ogni `extract_intent` call (cache MiniJinja built-in).


_UNDO_PATTERNS = (
    "annulla", "annullare", "annullo",
    "undo", "revert", "rollback", "ripristina",
    "torna indietro", "indietreggia", "anull",
)


def extract_intent(query: str, llm_call) -> Optional[dict]:
    """Estrae verb+object dalla richiesta. Ritorna None se LLM o parsing fallisce.

    Bypass deterministico per query di UNDO (annulla/undo/ripristina): il
    closed vocab non contiene "undo" come verbo, e il LLM tipicamente mappa
    "annulla" → "delete" (errato semanticamente). Ritorniamo verb=None per
    forzare fallback al lexicon ranking + iniezione builtin di undo_last_turn.
    """
    if not query or not query.strip():
        return None
    q_lower = query.lower()
    if any(p in q_lower for p in _UNDO_PATTERNS):
        return None  # signal "no canonical verb" → caller usa fallback
    prompt = prompt_loader.get(
        "intent_extractor",
        DEFAULT_LANG,
        verbs_inline=_vocab_verbs_inline(),
        objects_inline=_vocab_objects_inline(),
    )
    try:
        res = llm_call(prompt, query, max_tokens=80, think=False)
    except TypeError:
        # llm_call non supporta think kwarg; tenta senza
        try:
            res = llm_call(prompt, query, max_tokens=80)
        except Exception:
            return None
    except Exception:
        return None
    # Duck-type: accetta dict {"text": ...} (legacy) o ChatResult dataclass
    # (provider standard) o str (callback semplice).
    if isinstance(res, str):
        text = res
    elif hasattr(res, "text"):
        text = res.text or ""
    else:
        text = (res or {}).get("text") or ""
    parsed = _parse_json(text)
    if not parsed:
        return None
    # Compound: per una query multi-azione l'LLM ritorna una LISTA ordinata di
    # sotto-intenti (un dict {verb,object} per clausola). Normalizziamo OGNI
    # clausola al vocabolario chiuso e la conserviamo in `actions`: dispatch
    # rankizza il pool per-clausola con l'OGGETTO REALE di quella clausola
    # (es. "trova i processi"→object=processes), non un unico object globale.
    # Fix routing compound SENZA dizionari di sinonimi (multilingue via LLM).
    # Il PRIMO valido resta l'intent PRIMARIO per back-compat del ranking.
    def _norm_action(d):
        if not isinstance(d, dict):
            return None
        v = (d.get("verb") or "").strip().lower()
        o = (d.get("object") or "").strip().lower()
        if v not in VOCAB_VERBS:
            v = None
        if o not in VOCAB_OBJECTS:
            o = None
        if not v and not o:
            return None
        return {"verb": v, "object": o}

    actions: list[dict] = []
    if isinstance(parsed, list):
        for _d in parsed:
            _a = _norm_action(_d)
            if _a is not None:
                actions.append(_a)
        parsed = next((p for p in parsed if isinstance(p, dict)), None) or {}
    else:
        _a = _norm_action(parsed)
        if _a is not None:
            actions.append(_a)
    verb = (parsed.get("verb") or "").strip().lower()
    obj = (parsed.get("object") or "").strip().lower()
    if verb not in VOCAB_VERBS:
        verb = None
    if obj not in VOCAB_OBJECTS:
        obj = None
    # Universal §7.3: se LLM non produce object valido o produce "entries"
    # generico, prova Qwen3-Emb FT classifier (intent_classifier package).
    # Opt-in via env METNOS_INTENT_CLASSIFIER=1 (default OFF in prod).
    import os as _os
    if _os.environ.get("METNOS_INTENT_CLASSIFIER", "0") == "1":
        if not obj or obj == "entries":
            try:
                from runtime.intent_classifier import classify_query_object, is_available
                if is_available():
                    qwen_obj = classify_query_object(query, lang=DEFAULT_LANG)
                    if qwen_obj and qwen_obj in VOCAB_OBJECTS:
                        obj = qwen_obj
                        log.info("intent_classifier override: object=%s", obj)
            except Exception as e:
                log.warning("intent_classifier fail: %s", e)
    if not verb and not obj:
        return None
    out = {"verb": verb, "object": obj}
    # Esponi la decomposizione SOLO se compound reale (>=2 clausole distinte):
    # dispatch la usa per il ranking pool per-clausola. Mono-azione → assente
    # (back-compat: il ramo compound resta inattivo).
    if len(actions) >= 2:
        out["actions"] = actions
    # Enrichment ADR 0129: pattern intent-implicit. Detection deterministica
    # (§7.9) di azioni mutating implicite — sostantivi che realizzano un
    # OBJECT §2.2 in pipeline multi-azione dove manca il verbo mutating per
    # quel object. Il PLANNER usa `implicit_actions` come hint strutturato
    # (vedi `_core.j2` invariante IT/EN) per emettere lo step mutating
    # mancante senza decisione LLM. Lista vuota se niente di implicito.
    try:
        from vocab import detect_implicit_actions
        implicit = detect_implicit_actions(query)
        if implicit:
            out["implicit_actions"] = implicit
    except Exception as _ex:
        log.warning("detect_implicit_actions failed: %s", _ex)
    return out


def _parse_json(text: str) -> Optional[dict]:
    """Parser tollerante: tenta json.loads su tutto il blocco, poi su substring
    `{...}` piu' lunga, poi su pattern verb/object esplicito."""
    if not text:
        return None
    # 1. Pulisci markdown fence se presente
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
    t = re.sub(r"\n?```\s*$", "", t)
    # 2. Tenta parse diretto
    try:
        return json.loads(t)
    except Exception as _e:  # silent swallow (auto-fixed)
        log.warning("silent exception in %s: %s", __name__, _e)
    # 3. Estrai oggetto JSON con regex
    m = re.search(r"\{[^{}]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
    # 4. Pattern key:value separati
    verb_m = re.search(r'"verb"\s*:\s*"([a-z]+)"', t)
    obj_m = re.search(r'"object"\s*:\s*"([a-z]+)"', t)
    if verb_m or obj_m:
        return {
            "verb": verb_m.group(1) if verb_m else None,
            "object": obj_m.group(1) if obj_m else None,
        }
    return None
