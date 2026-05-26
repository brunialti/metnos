"""Pronoia (Πρόνοια) — provvidenza che salva dallo stallo (ADR 0161 ext).

    «Pronoia (Πρόνοια): provvidenza divina. Etimologicamente pro-noûs,
     "l'intelligenza che vede prima". Madre di Prometeo nel mito.
     Interviene quando Mētis (consiglio interno) e Noûs (esecuzione)
     falliscono — porta soccorso provvidenziale.»

Nella tetrade Praxis Engine:
  Mētis   (Μῆτις)   propose framework            → praxis_propose.py
  Noûs    (νοῦς)    execute deterministico       → praxis_executor.py
  Praxis  (πρᾶξις)  ricorda + auto-promote       → praxis.py
  Pronoia (Πρόνοια) ULTIMA INSTANZA quando 3 sopra falliscono → questo modulo

Pattern: classify_error(praxis_run) → seleziona prompt specializzato per
classe di errore → ri-propone framework via Mētis stesso modello (Gemma
26B locale) ma con context-aware recovery prompt. Se successo →
osservazione salvata in Praxis cache → next time Mētis sa direttamente
(Pronoia tace, come nel mito).

General purpose §7.3: prompt specializzati PER CLASSE DI ERRORE STRUTTURALE,
NON per domain. Aggiungere nuovo executor → Pronoia lo vede uguale.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

log = logging.getLogger(__name__)


# Classi di errore strutturali (universali, non per-domain).
# Set ortogonale minimo (3 classi recoverable + 1 fallback).
# A/B/C distinguono cosa serve fare; D = Pronoia non interviene.
ERROR_CLASSES = (
    "wrong_tool",      # A: tool inadatto (hallucinated, crash, semanticamente wrong)
    "wrong_args",      # B: args sbagliati/mancanti, pipeline malformata
    "missing_input",   # C: backend/index/path missing, output vuoto post-filter
    "out_of_scope",    # D: NON recoverable (env limit, capability totalmente assente)
)

# Pattern fail → classe ortogonale
_OUT_OF_SCOPE_MARKERS = (
    # Telegram location share required (utente deve agire fisicamente)
    "no location received yet",
    "condividi una posizione su telegram",
    "share your location",
    # GPS coords mancanti (variante del location share)
    "pass either 'coords'",
    "missing required arg: pass",  # pattern argomenti mutually-exclusive non forniti
    # Capabilities veramente missing (NO admin/synth fallback)
    "needs_inputs",  # dialog richiesto, non recoverable senza utente
    "needs user input",
)


_PROVIDER_QUERY_MARKERS = (
    "drive", "gmail", "google calendar", "github",
    "google drive", "g suite", "workspace",
)


def _query_mentions_provider(query: str) -> bool:
    q = (query or "").lower()
    return any(m in q for m in _PROVIDER_QUERY_MARKERS)


def classify_error(praxis_run, query: str = "") -> str:
    """Classifica error type da PraxisRun. Deterministico §7.9.

    Args:
      praxis_run: PraxisRun con `steps` e `aborted_reason`.

    Returns:
      str in ERROR_CLASSES.
    """
    if not praxis_run:
        return "generic"
    aborted = (praxis_run.aborted_reason or "").lower()
    # Cap/loop = pipeline malformata → B. wrong_args (set ortogonale)
    if "cap_steps" in aborted or "cap_same" in aborted or "loop" in aborted:
        return "wrong_args"
    if not praxis_run.steps:
        return "wrong_args"  # no step eseguiti: framework probabilmente vuoto
    last = praxis_run.steps[-1]
    res = last.result if isinstance(last.result, dict) else {}
    ec = (res.get("error_class") or res.get("error_code") or "").lower()
    err_txt = str(res.get("error") or "").lower()

    # D. OUT_OF_SCOPE — Pronoia NON interviene (env limit, no recovery possibile)
    if any(m in err_txt for m in _OUT_OF_SCOPE_MARKERS):
        return "out_of_scope"

    # D'. OUT_OF_SCOPE provider missing: query menziona provider esterno
    # (drive/gmail/calendar/github) MA Mētis ha proposto tool LOCALE
    # (find_files/find_dirs) e fallisce → skill provider non caricata.
    # Aporia missing_skill suggested_action.
    if _query_mentions_provider(query) and (
            "argomento obbligatorio" in err_txt
            or "percorso non trovato" in err_txt
            or "path not found" in err_txt):
        return "out_of_scope"

    # A. WRONG_TOOL — tool hallucinato/crashato/semanticamente sbagliato
    if (ec == "tool_unknown" or ec == "exception"
            or "tool unknown" in err_txt
            or "raised" in err_txt or "traceback" in err_txt):
        return "wrong_tool"

    # C. MISSING_INPUT — backend/index/path missing, output vuoto
    if ("no indexed dirs" in err_txt or "index missing" in err_txt
            or ec == "index_missing"
            or "percorso non trovato" in err_txt
            or "path not found" in err_txt
            or "no such file or directory" in err_txt
            or "directory non esistente" in err_txt):
        return "missing_input"

    # B. WRONG_ARGS — args sbagliati, pipeline malformata
    if (ec == "invalid_args" or ec == "err_arg_missing"
            or "missing required arg" in err_txt
            or "argomento obbligatorio" in err_txt
            or "argomento non valido" in err_txt
            or "must be a non-empty list" in err_txt
            or "must be a list" in err_txt
            or "missing or invalid" in err_txt):
        return "wrong_args"

    # C. EMPTY post-filter (input vuoto) → missing_input
    entries_returned = res.get("entries") or []
    if (praxis_run.ok_count == 0
            or (isinstance(entries_returned, list) and len(entries_returned) == 0
                and last.ok)):
        return "missing_input"

    # Fallback: trattalo come wrong_args (pipeline non-ottimale)
    return "wrong_args"


def is_recoverable(error_class: str) -> bool:
    """Pronoia interviene SOLO sulle 3 classi A/B/C. Su D (out_of_scope)
    fail onesto senza retry."""
    return error_class in ("wrong_tool", "wrong_args", "missing_input")


def _dbg_trace(stage: str, **fields) -> None:
    """Trace Pronoia diagnostic. Append JSONL in PATH_USER_DATA."""
    try:
        import json as _json
        from datetime import datetime, timezone
        try:
            import config as _C
            path = _C.PATH_USER_DATA / "pronoia_trace.jsonl"
        except Exception:
            from pathlib import Path
            path = Path.home() / ".local" / "share" / "metnos" / "pronoia_trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now(timezone.utc).isoformat(),
                "stage": stage, **fields}
        with open(path, "a") as f:
            f.write(_json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def intervene(*, query: str, intent: dict, catalog: list,
               praxis_run, invoke_executor_cb: Callable,
               llm_call_wise: Optional[Callable] = None,
               llm_call_fast: Optional[Callable] = None,
               remediate_args_cb: Optional[Callable] = None,
               lang: str = "it",
               verbose: bool = False) -> Optional[dict]:
    """Pronoia interviene: classifica → ri-propone con prompt specializzato → execute.

    Args:
      query, intent, catalog, invoke_executor_cb, llm_call_*: come try_praxis_path.
      praxis_run: PraxisRun del tentativo fallito (per classify + context).

    Returns:
      dict come try_praxis_path, o None se Pronoia non riesce a recuperare.
    """
    try:
        from praxis import compute_intent_sig, extract_keywords
        from praxis_propose import propose_framework
        from praxis_executor import execute_framework
    except Exception as ex:
        log.warning("pronoia: import failed: %r", ex)
        return None

    err_class = classify_error(praxis_run, query=query)
    if verbose:
        print(f"[pronoia] intervene: error_class={err_class!r}")
    _dbg_trace("classify", query=query, error_class=err_class,
                aborted=praxis_run.aborted_reason or "")

    # D. OUT_OF_SCOPE → Aporia: classify + log + suggested_action.
    if not is_recoverable(err_class):
        if verbose:
            print(f"[pronoia] error_class={err_class} NOT recoverable → Aporia")
        try:
            from praxis import compute_intent_sig
            import aporia
            verb = intent.get("verb", "") if intent else ""
            obj = intent.get("object", "") if intent else ""
            keywords = intent.get("keywords") or [] if intent else []
            isig, ihash = compute_intent_sig(verb, obj, keywords)
            last_step = praxis_run.steps[-1] if praxis_run.steps else None
            err_text = (last_step.result.get("error", "")
                         if last_step and isinstance(last_step.result, dict)
                         else "")
            lacuna = aporia.record(query, ihash, isig, err_text)
            # Honest final answer con suggested_action
            final_text = (f"Non posso risolvere: {lacuna['root_cause']}. "
                           f"Per procedere: {lacuna['suggested_action']}")
            return {
                "steps": praxis_run.steps,
                "final_text": final_text,
                "final_kind": "answer",  # answer onesto, non error
                "framework": {},
                "framework_hash": "",
                "intent_sig": isig,
                "verb": verb,
                "object": obj,
                "keywords": keywords,
                "match_source": "aporia",
                "elapsed_ms": 0,
                "aborted_reason": "",
                "lacuna_id": lacuna["lacuna_id"],
                "root_cause": lacuna["root_cause"],
            }
        except Exception as ex:
            log.warning("aporia integration failed: %r", ex)
            return None

    # Build recovery context from PraxisRun for prompt
    last_step = praxis_run.steps[-1] if praxis_run.steps else None
    recovery_context = {
        "error_class": err_class,
        "tool_used": last_step.tool if last_step else "",
        "args_used": last_step.args if last_step else {},
        "error_text": (last_step.result.get("error", "")
                        if last_step and isinstance(last_step.result, dict)
                        else ""),
        "step_idx": last_step.step_idx if last_step else 0,
        "framework_tried": [s.tool for s in praxis_run.steps],
        "aborted_reason": praxis_run.aborted_reason or "",
    }

    # Exclude failed framework_hash from re-propose
    from praxis import compute_framework_hash
    failed_hash = compute_framework_hash({
        "steps": [{"tool": s.tool, "args": s.args}
                   for s in praxis_run.steps],
    })

    verb = (intent.get("verb") or "").strip()
    obj = (intent.get("object") or "").strip()
    keywords = intent.get("keywords") or []

    # 1. Provider filter universale (ADR 0136 reverse): pool coerente con
    # query marker (drive/gmail/github/calendar/etc).
    try:
        from tool_grammar import filter_pool_for_grammar
        filtered_catalog, _excl = filter_pool_for_grammar(
            catalog, user_query=query, proximity_markers=()
        )
    except Exception:
        filtered_catalog = catalog

    # 2. Escludi il tool fallito dal pool (Pronoia non lo ri-propone).
    failed_tool = recovery_context.get("tool_used", "")
    if failed_tool:
        filtered_catalog = [
            e for e in filtered_catalog
            if getattr(e, "name", None) != failed_tool
        ]

    available = [e.name for e in filtered_catalog
                  if getattr(e, "name", None)]
    if "final_answer" not in available:
        available.append("final_answer")
    # request_new_executor sempre disponibile per Pronoia (out-of-scope cases)
    if "request_new_executor" not in available:
        available.append("request_new_executor")

    # Pronoia tier: default "wise" (Gemma 26B locale), scalabile "frontier"
    # via env METNOS_PRONOIA_TIER (configurabile anche da UI admin).
    import os as _os
    pronoia_tier = _os.environ.get("METNOS_PRONOIA_TIER", "wise").lower()
    if pronoia_tier not in ("wise", "frontier"):
        pronoia_tier = "wise"

    # Wrappa llm_call con tier_override quando richiesto
    if pronoia_tier == "frontier" and llm_call_wise is not None:
        original_llm_call = llm_call_wise
        def _pronoia_call(sys_msg, user_msg, **kw):
            kw["tier_override"] = "frontier"
            return original_llm_call(sys_msg, user_msg, **kw)
        llm_call_for_pronoia = _pronoia_call
    else:
        llm_call_for_pronoia = llm_call_wise

    if verbose:
        print(f"[pronoia] tier={pronoia_tier}")
    _dbg_trace("tier", query=query, tier=pronoia_tier)

    try:
        framework = propose_framework(
            query=query,
            intent={"verb": verb, "object": obj, "keywords": keywords},
            available_tools=available,
            excluded_frameworks={failed_hash},
            llm_call=llm_call_for_pronoia,
            lang=lang,
            use_grammar=False,  # Pronoia: parser tollerante (recovery flessibile)
            recovery_context=recovery_context,
        )
    except TypeError:
        # propose_framework non supporta recovery_context (fallback)
        framework = propose_framework(
            query=query,
            intent={"verb": verb, "object": obj, "keywords": keywords},
            available_tools=available,
            excluded_frameworks={failed_hash},
            llm_call=llm_call_for_pronoia,
            lang=lang,
        )
    except Exception as ex:
        log.warning("pronoia: propose failed: %r", ex)
        return None

    if not framework:
        if verbose:
            print("[pronoia] propose returned None (parse failed)")
        _dbg_trace("parse_fail", query=query, error_class=err_class)
        return None

    if verbose:
        print(f"[pronoia] PROPOSE recovery → {len(framework.get('steps') or [])} steps")
    _dbg_trace("propose",
                query=query, error_class=err_class,
                failed_tool=recovery_context["tool_used"],
                framework_steps=[{"tool": s.get("tool"), "args": s.get("args")}
                                  for s in (framework.get("steps") or [])],
                pool_size=len(available),
                pool_sample=available[:30])

    # Execute with same Noûs
    _, intent_hash = compute_intent_sig(verb, obj, keywords)
    run = execute_framework(
        framework,
        invoke_executor=invoke_executor_cb,
        llm_call_fast=llm_call_fast,
        query_context=query,
        remediate_args_cb=remediate_args_cb,
        intent_hash=intent_hash,
    )

    # Aggiorna result con tier usato
    pronoia_tier_used = pronoia_tier

    if verbose:
        print(f"[pronoia] exec: {len(run.steps)} steps, ok={run.ok_count}, "
               f"elapsed={run.elapsed_ms}ms, aborted={run.aborted_reason!r}")
    _dbg_trace("execute",
                query=query, error_class=err_class,
                kind=run.final_kind,
                ok_count=run.ok_count,
                aborted=run.aborted_reason or "",
                steps_executed=[
                    {"tool": s.tool,
                      "ok": s.result.get("ok") if isinstance(s.result, dict) else None,
                      "err": (s.result.get("error", "") if isinstance(s.result, dict) else "")[:100]}
                    for s in run.steps
                ])

    return {
        "steps": run.steps,
        "final_text": run.final_text,
        "final_kind": run.final_kind,
        "framework": framework,
        "framework_hash": run.framework_hash,
        "intent_sig": "",
        "verb": verb,
        "object": obj,
        "keywords": keywords,
        "match_source": "pronoia",
        "elapsed_ms": run.elapsed_ms,
        "aborted_reason": run.aborted_reason,
        "error_class": err_class,
    }
