#!/usr/bin/env python3
"""intent_extractor.py — estrae verbo+oggetto canonici da una richiesta utente.

Approccio: una chiamata LLM (modello locale fast/middle tier, think=False, ~500ms)
con prompt minimo che chiede al modello di mappare la richiesta sul vocabolario
chiuso di Metnos (20 verbi × 11 oggetti).

Pipeline:
    query → LLM → {verb, object, confidence}
                → caller usa per filtrare candidates

Vantaggi rispetto al lexicon match:
- Robusto a variazioni di linguaggio (IT/EN, conjugazioni, sinonimi, idiomi).
- Cross-language (il modello locale e' multilingue).
- Non richiede manutenzione di un dizionario manuale.

Latenza: ~500-800ms con il modello locale think=False, num_predict=80.

Failure mode:
- LLM down → ritorna None, caller deve fall-back al lexicon.
- LLM produce JSON malformato → parser tenta recupero best-effort, altrimenti None.
- LLM produce verbo/oggetto fuori vocabolario → ritorna None (non si forza).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from vocab import (
    ACTIONS as VOCAB_VERBS,
    OBJECTS as VOCAB_OBJECTS,
    render_actions_inline as _vocab_verbs_inline,
    render_objects_inline as _vocab_objects_inline,
    render_boundaries as _vocab_boundaries,
)
from logging_setup import get_logger
log = get_logger(__name__)

import prompt_loader
import detection_lexicon as _dl
from config import DEFAULT_LANG

# Budget token dell'estrazione intent. 80 bastava per il mono (JSON corto) ma
# TRONCAVA la decomposizione COMPOUND (array multi-clausola) → JSON malformato
# → actions=[] → guard skeleton/enforce affamati (bug FASE 3 publish, 18/6).
# 320 copre ~7 clausole; il mono resta invariato (il modello chiude il JSON
# molto prima). §7.3 generale, non patch per-query.
_INTENT_MAX_TOKENS = 320

# Prompt persistito in `runtime/prompts/<lang>/intent_extractor_v4.j2` (ADR 0092
# Phase 2; v3 `intent_extractor.j2` ritirato 26/6, §7.1 — v4 unico template mono).
# Renderizzato lazy a ogni `extract_intent` call (cache MiniJinja built-in).


# Bypass UNDO migrato a detection_lexicon (concept substring
# `undo.intent_bypass`); vedi detection_lexicon_seed.


def extract_intent(query: str, llm_call) -> Optional[dict]:
    """Estrae verb+object dalla richiesta. Ritorna None se LLM o parsing fallisce.

    Bypass deterministico per query di UNDO (annulla/undo/ripristina): il
    closed vocab non contiene "undo" come verbo, e il LLM tipicamente mappa
    "annulla" → "delete" (errato semanticamente). Ritorniamo verb=None per
    forzare fallback al lexicon ranking + iniezione builtin di undo_last_turn.
    """
    if not query or not query.strip():
        return None
    if _dl.match("undo.intent_bypass", query):
        return None  # signal "no canonical verb" → caller usa fallback
    # v4 è il template UNICO del path mono (v3 ritirato 26/6, §7.1): i CONFINI-VERBO
    # arrivano verbatim dal SoT (vocab.render_boundaries) → coerenza def↔prompt per
    # costruzione, niente twin scritto a mano che driftava (v3 citava `fetch`, verbo
    # rimosso §2.2). Pilota ratificato (mono 83.5%→98.8%, gate routing 29/29).
    #
    # METNOS_INTENT_SCAFFOLD=1 (24/6, hybrid anaphora-aware, pilota compound, default 0):
    # segmentazione DETERMINISTICA (split_query_chunks) → segmenti numerati nel
    # prompt → l'LLM emette {n,ref,verb,object} risolvendo l'ANAFORA (clitici
    # -lo/-le, oggetti elisi) sulla query INTERA → consumer riconcilia il count vs
    # i chunk e riempie i buchi col detector lessicale. §7.9: codice possiede
    # segmentazione+count, LLM possiede anafora+boundary. Solo su compound (>=2
    # segmenti); mono → path v4 invariato.
    _scaffold = os.getenv("METNOS_INTENT_SCAFFOLD", "0") == "1"
    _segments: list[str] = []
    if _scaffold:
        try:
            from compound_decomposer import split_query_chunks
            _segments = split_query_chunks(query)
        except Exception:
            _segments = []
    # Falso-compound: lo split può spezzare su una «e» che coordina AVVERBI/
    # frammenti senza azione («che ora E adesso», «qui E ora») → 2 segmenti ma
    # UNA sola query. Lo scaffold richiede ≥2 segmenti che portino DAVVERO
    # un'azione (verbo canonico rilevabile). Sotto 2 → path mono (v4), dove le
    # iniezioni text-driven a query-intera (get_now, EXIF) restano intatte.
    # §7.9 deterministico, language-agnostic (usa il detector verbi del vocab).
    if _scaffold and len(_segments) >= 2:
        try:
            from prefilter import tokenize as _tk, detect_canonical_verbs_all as _vb
            _action_segs = sum(1 for s in _segments if _vb(_tk(s)))
        except Exception:
            _action_segs = len(_segments)
        if _action_segs < 2:
            _segments = []  # non è un vero compound → ricadi su mono
    if _scaffold and len(_segments) >= 2:
        segments_block = "\n".join(f"{i}. {s}" for i, s in enumerate(_segments, 1))
        prompt = prompt_loader.get(
            "intent_extractor_scaffold",
            DEFAULT_LANG,
            verbs_inline=_vocab_verbs_inline(),
            objects_inline=_vocab_objects_inline(),
            boundaries_block=_vocab_boundaries(DEFAULT_LANG),
            segments_block=segments_block,
        )
    else:
        prompt = prompt_loader.get(
            "intent_extractor_v4",
            DEFAULT_LANG,
            verbs_inline=_vocab_verbs_inline(),
            objects_inline=_vocab_objects_inline(),
            boundaries_block=_vocab_boundaries(DEFAULT_LANG),
        )
    try:
        res = llm_call(prompt, query, max_tokens=_INTENT_MAX_TOKENS, think=False)
    except TypeError:
        # llm_call non supporta think kwarg; tenta senza
        try:
            res = llm_call(prompt, query, max_tokens=_INTENT_MAX_TOKENS)
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
    # Scaffold hybrid: l'LLM ritorna {"clauses":[{n,ref,verb,object}, ...]}.
    # Riduciamo a LISTA ordinata di {verb,object} (ref/n droppati: ref forza la
    # risoluzione anafora in GENERAZIONE, n è l'ancora di conteggio). Da qui il
    # flusso `actions` resta identico (back-compat consumer).
    if isinstance(parsed, dict) and isinstance(parsed.get("clauses"), list):
        parsed = [
            {"verb": c.get("verb"), "object": c.get("object")}
            for c in parsed["clauses"] if isinstance(c, dict)
        ]
    # Compound: per una query multi-azione l'LLM ritorna una LISTA ordinata di
    # sotto-intenti (un dict {verb,object} per clausola). Normalizziamo OGNI
    # clausola al vocabolario chiuso e la conserviamo in `actions`: dispatch
    # rankizza il pool per-clausola con l'OGGETTO REALE di quella clausola
    # (es. "trova i processi"→object=processes), non un unico object globale.
    # Fix routing compound SENZA dizionari di sinonimi (multilingue via LLM).
    # Il PRIMO valido resta l'intent PRIMARIO per back-compat del ranking.
    def _norm_action(d, ctx_text=None):
        if not isinstance(d, dict):
            return None
        v = (d.get("verb") or "").strip().lower()
        o = (d.get("object") or "").strip().lower()
        if v not in VOCAB_VERBS:
            v = None
        if o not in VOCAB_OBJECTS:
            o = None
        # Routability §7.9 (24/6): se `verb_obj` non ha executor reale, rimappa
        # l'object — carrier §2.2 (images/texts→files) o oggetto reale dal TESTO
        # del segmento (pdf→files, cartella→dirs). Verità = presenza on-disk, non
        # lista. Chiude le combo morte (compress_images, get_numbers, send_images).
        # ctx_text = `ref` del clause scaffold o il segmento (context-aware).
        # NB (24/6, analisi routing): l'intent è un livello SEMANTICO, NON una
        # chiave di match-esatto-su-disco. Il dispatch (build_routing_pool →
        # rank_with_intent, routing_pool.py) risolve (verb,object) contro il
        # catalogo reale: object-family completion + _OBJECT_PRIMARY_TOOLS +
        # precursori → (compress,images) routa a compress_files, ecc. Quindi NON
        # forziamo qui il livello-executor (era un errore: rompeva list/files vs
        # find/files, livelli semantici distinti). L'unico fix legittimo a monte
        # è correggere le §2.2-VIOLAZIONI vere (find_processes→get, ecc.), che
        # vivono nel prompt/boundary, non in un rimappatore meccanico.
        if not v and not o:
            return None
        return {"verb": v, "object": o}

    actions: list[dict] = []
    if isinstance(parsed, list):
        for _i, _d in enumerate(parsed):
            # ctx_text: `ref` (anafora risolta) del clause, o il segmento i-esimo
            _ctx = (_d.get("ref") if isinstance(_d, dict) else None)
            if not _ctx and _scaffold and _i < len(_segments):
                _ctx = _segments[_i]
            _a = _norm_action(_d, _ctx)
            if _a is not None:
                actions.append(_a)
        parsed = next((p for p in parsed if isinstance(p, dict)), None) or {}
    else:
        _a = _norm_action(parsed, query)
        if _a is not None:
            actions.append(_a)
    # Riconciliazione count (scaffold §7.9): se l'LLM ha emesso MENO clausole dei
    # segmenti deterministici, riempi i buchi col detector lessicale per-chunk
    # (detect_chunk_action) — garantisce che nessuna clausola sia silenziosamente
    # persa (fallimento FASE-3 publish 18/6). Se ne ha emesse di PIÙ, tronca ai
    # segmenti. Allineamento posizionale (l'ordine segmenti = ordine esecuzione).
    if _scaffold and len(_segments) >= 2 and len(actions) != len(_segments):
        try:
            from compound_decomposer import detect_chunk_action
            fixed: list[dict] = []
            for i, seg in enumerate(_segments):
                if i < len(actions):
                    fixed.append(actions[i])
                else:
                    det = detect_chunk_action(seg)
                    if det:
                        _a = _norm_action({"verb": det[0], "object": det[1]})
                        if _a:
                            fixed.append(_a)
            if fixed:
                actions = fixed
        except Exception:
            pass

    verb = (parsed.get("verb") or "").strip().lower()
    obj = (parsed.get("object") or "").strip().lower()
    if verb not in VOCAB_VERBS:
        verb = None
    if obj not in VOCAB_OBJECTS:
        obj = None
    # Scaffold: l'intent PRIMARIO (verb/obj) viene dalla 1ª clausola riconciliata
    # (parsed potrebbe essere {clauses:...} senza verb/object top-level).
    if _scaffold and actions and not verb and not obj:
        verb = actions[0].get("verb")
        obj = actions[0].get("object")
    # L'estrattore LLM grammar-based (objects_inline + GBNF) e' l'UNICO
    # classificatore d'object (17/6/2026): rimosso l'override Qwen3-Emb FT
    # (intent_classifier package) — anchors per-lingua hardcoded = anti-pattern
    # i18n, e il solo LLM copre il gold 25/25. Vedi rimozione anchors.py.
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
