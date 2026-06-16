#!/usr/bin/env python3
"""nlu — estrattore NLU CENTRALIZZATO (proposta drop-in, sostituisce i regex).

PROGETTO (non wired). Idea: UNA sola fonte per l'estrazione di struttura dalla
frase utente, che rimpiazza i lessici/regex pre-cablati sparsi
(ordering_clause, time_window, recurring_tasks, output_policy count/visualize).

PRINCIPI
  1. UNA call LLM vincolata a schema per query, MEMOIZZATA: piu' consumatori
     nello stesso turno (ordering + time + count) condividono UNA estrazione
     → nessuna proliferazione di call. Deterministico (§7.9: temp0 + seed).
  2. DROP-IN: le funzioni pubbliche esistenti diventano deleganti di 1 riga;
     gli adapter restituiscono ESATTAMENTE le shape legacy → i chiamanti a
     monte non cambiano.
  3. MINIMO IMPATTO + REVERSIBILE: gate `METNOS_NLU` (llm|regex|off). Se l'LLM
     non e' raggiungibile o `off`, ricade sul fallback regex registrato →
     zero rischio di rottura totale durante il rollout.
  4. UNA spec, multilingue: lo schema (frame semantico) e' lingua-indipendente;
     l'input in qualunque lingua lo capisce il modello (no grammatica/regex
     per-lingua). Prompt in inglese (regola funzioni language-independent).

CONFINE: i formati-macchina lingua-invarianti (path, date ISO, numeri) restano
a parser deterministici; qui solo l'estrazione da LINGUAGGIO NATURALE.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

# Nel modulo reale: from . import llm_helpers; qui riuso il client POC isolato.
from nlu_extract import METNOS_NLU_SCHEMA, METNOS_NLU_INSTRUCTION, extract
import i18n as _i18n  # solo per current_lang(); nessun'altra dipendenza


def _gate() -> str:
    """llm (default) | regex | off — scelta del meccanismo a runtime."""
    return os.environ.get("METNOS_NLU", "llm").lower()


# ── cache per-query (condivisione fra consumatori dello stesso turno) ────────
_CACHE: dict[tuple, Optional[dict]] = {}
_CACHE_CAP = 256


def clear_cache() -> None:
    """Da chiamare al confine di turno se si vuole forzare ri-estrazione."""
    _CACHE.clear()


def frame(query: str) -> Optional[dict]:
    """Frame strutturato della query (o None se gate=off / LLM ko).

    Memoizzato per (query, lingua, gate). UNA sola estrazione LLM condivisa
    da tutti gli adapter. Il JSON e' valido per costruzione (constrained).
    """
    q = (query or "").strip()
    if not q or _gate() != "llm":
        return None
    key = (q, _i18n.current_lang())
    if key in _CACHE:
        return _CACHE[key]
    data, meta = extract(q, METNOS_NLU_SCHEMA, METNOS_NLU_INSTRUCTION,
                         max_tokens=200)
    out = data if meta.get("ok") else None
    if len(_CACHE) >= _CACHE_CAP:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = out
    return out


# ── adapter drop-in: shape IDENTICA alle funzioni legacy ─────────────────────
def ordering(query: str, *, fallback: Callable | None = None) -> Optional[dict]:
    """== ordering_clause.detect: {mode, key_text, desc} | None."""
    f = frame(query)
    if f is None:
        return fallback(query) if fallback else None
    o = f.get("ordering") or {}
    if o.get("mode", "none") == "none":
        return None
    return {"mode": o["mode"], "key_text": (o.get("key") or "").strip().lower(),
            "desc": bool(o.get("desc"))}


def time_window(query: str, *, fallback: Callable | None = None) -> Optional[str]:
    """== time_window_resolver.parse_query_time_window: str | None.
    NB: il valore normalizzato deve appartenere al vocabolario accettato dal
    resolver a valle (today|last-Nd|next-Nh|range); validare in integrazione."""
    f = frame(query)
    if f is None:
        return fallback(query) if fallback else None
    tw = (f.get("time_window") or "").strip()
    return tw or None


def recurrence(query: str, *, fallback: Callable | None = None) -> Optional[dict]:
    """== recurring_tasks.parse_recurrence_query: dict | None."""
    f = frame(query)
    if f is None:
        return fallback(query) if fallback else None
    rec = f.get("recurrence") or {}
    every = (rec.get("every") or "").strip()
    if not every:
        return None
    return {"every": every, "at": (rec.get("at") or "").strip()}


def count_intent(query: str, *, fallback: Callable | None = None) -> bool:
    f = frame(query)
    if f is None:
        return fallback(query) if fallback else False
    return bool(f.get("count_intent"))


def visualize_intent(query: str, *, fallback: Callable | None = None) -> bool:
    f = frame(query)
    if f is None:
        return fallback(query) if fallback else False
    return bool(f.get("visualize_intent"))
