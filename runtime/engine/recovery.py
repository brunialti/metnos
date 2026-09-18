"""engine/recovery.py — Protocol + SimpleRecovery (default).

Recovery interviene dopo Executor errore. Classifica error → produce
nuovo Framework via Proposer escludendo failed path.

§7.3 universalità: classifier deterministic via error_class strutturato
nel result.error_class. No regex su error_text multi-lingua.
"""
from __future__ import annotations

import logging
from typing import Optional, Callable, Protocol

from .types import (Intent, Framework, RunResult, ERROR_CLASSES, RECOVERABLE,
                    OPERATIONAL_ERROR_CLASSES, result_error_classes)

log = logging.getLogger(__name__)


# ── Protocol ──────────────────────────────────────────────────────────────

class Recovery(Protocol):
    """Tenta recovery dopo execute fallito."""
    def recover(self, *, failed_run: RunResult, query: str, intent: Intent,
                pool: list[str], proposer,
                llm_call: Optional[Callable] = None,
                lang: str = "it",
                catalog: Optional[list] = None) -> Optional[Framework]: ...


# ── classify_error deterministic ──────────────────────────────────────────

def classify_error(failed_run: RunResult) -> str:
    """Mappa RunResult fallito a 4 classi (3 recoverable + out_of_scope).

    Priorità: legge result.error_class strutturato. Fallback su pattern
    aborted_reason (cap/loop). Mai regex su error_text.
    """
    if not failed_run:
        return "out_of_scope"
    aborted = (failed_run.aborted_reason or "").lower()
    # Cap/loop strutturali: framework rotto, anche senza step
    if "cap_steps" in aborted or "cap_same" in aborted or "loop" in aborted:
        return "wrong_args"
    if not failed_run.steps:
        return "out_of_scope"
    last = failed_run.steps[-1]
    r = last.result if isinstance(last.result, dict) else {}
    classes = result_error_classes(r)
    ec = classes[0] if classes else ""
    if ec in ERROR_CLASSES:
        return ec
    # Una ricerca conclusa senza risultati pertinenti non prova che gli
    # argomenti siano malformati. La classe strutturale piu' vicina e'
    # ``missing_input``: abilita un unico tentativo alternativo, poi il
    # terminatore conserva il codice executor concreto ``search_no_results``.
    if ec == "search_no_results":
        return "missing_input"
    # Un errore operativo puo' essere annidato in ``failed[]`` nei producer
    # vettoriali. Un piano alternativo non ripara DNS, timeout o sidecar:
    # chiudi senza recovery invece di etichettarlo come argomenti errati.
    if any(item in OPERATIONAL_ERROR_CLASSES for item in classes):
        return "out_of_scope"
    # Out_of_scope: executor ha esplicito needs_user_action / capability_missing
    if ec in ("needs_user_action", "capability_missing"):
        return "out_of_scope"
    # Fallback strutturale: ok_count=0 con last step ok → input vuoto
    if failed_run.ok_count == 0 and last.ok:
        entries = r.get("entries") if isinstance(r, dict) else None
        if isinstance(entries, list) and not entries:
            return "missing_input"
    # Default: wrong_args (pipeline malformata)
    return "wrong_args"


def is_recoverable(err_class: str) -> bool:
    return err_class in RECOVERABLE


def recovery_signal_for(failed_run: RunResult) -> dict | None:
    """Return bounded, structured context for a failure-specific retry.

    The signal contains no snippets or URLs.  It lets the proposer distinguish
    a backend with zero candidates from candidates rejected by relevance or by
    later filters, without parsing localized error prose.
    """
    for step in reversed(failed_run.steps or []):
        result = step.result if isinstance(step.result, dict) else {}
        if "search_no_results" not in result_error_classes(result):
            continue
        metadata = result.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        rerank = metadata.get("rerank")
        rerank = rerank if isinstance(rerank, dict) else {}
        backend_count = metadata.get("backend_result_count")
        accepted_count = metadata.get("search_results_used")
        if backend_count == 0:
            stage = "backend_empty"
        elif rerank.get("reason") == "no_relevant_candidates":
            stage = "relevance_rejected"
        elif isinstance(accepted_count, int) and accepted_count > 0:
            stage = "post_filter_empty"
        else:
            stage = "no_accepted_results"
        args = step.args if isinstance(step.args, dict) else {}
        attempted_query = (
            args.get("search_query") or args.get("query")
            or result.get("search_query") or metadata.get("search_query") or ""
        )
        return {
            "kind": "search_no_results",
            "failure_stage": stage,
            "attempted_query": str(attempted_query).strip(),
            "candidate_count": (
                int(backend_count) if isinstance(backend_count, int) else None
            ),
            "accepted_count": (
                int(accepted_count) if isinstance(accepted_count, int) else 0
            ),
        }
    return None


def propose_with_recovery_signal(*, proposer, intent: Intent,
                                 signal: dict | None, **kwargs):
    """Expose retry context only for the duration of one proposer call."""
    marker = object()
    previous = getattr(intent, "_recovery_signal", marker)
    try:
        if signal:
            setattr(intent, "_recovery_signal", signal)
        return proposer.propose(intent=intent, **kwargs)
    finally:
        if previous is marker:
            try:
                delattr(intent, "_recovery_signal")
            except AttributeError:
                pass
        else:
            setattr(intent, "_recovery_signal", previous)


# ── SimpleRecovery ────────────────────────────────────────────────────────

class SimpleRecovery:
    """Default: 1 retry via Proposer escludendo failed_hash. No multi-strategy."""

    def recover(self, *, failed_run: RunResult, query: str, intent: Intent,
                pool: list[str], proposer,
                llm_call: Optional[Callable] = None,
                lang: str = "it",
                catalog: Optional[list] = None) -> Optional[Framework]:
        err = classify_error(failed_run)
        if not is_recoverable(err):
            return None  # out_of_scope → terminator
        signal = recovery_signal_for(failed_run)
        # Le esclusioni normali sono per SHAPE. Una ricerca riformulata usa per
        # forza la stessa shape ``find_urls(search_query)``: in quel caso lascia
        # la shape disponibile e demanda il blocco del duplicato esatto al
        # fingerprint esecutivo del dispatcher.
        failed_hash = failed_run.framework_hash
        excluded = ({failed_hash} if failed_hash else set()) if not signal else set()
        # Esclude anche tool del last step (probabile causa)
        excluded_pool = pool
        if failed_run.steps and not signal:
            failed_tool = failed_run.steps[-1].tool
            if failed_tool:
                excluded_pool = [t for t in pool if t != failed_tool]
        try:
            return propose_with_recovery_signal(
                proposer=proposer, intent=intent, signal=signal,
                query=query, pool=excluded_pool,
                excluded_hashes=excluded, llm_call=llm_call, lang=lang,
                catalog=catalog,
            )
        except Exception as ex:
            log.warning("SimpleRecovery propose failed: %r", ex)
            return None


# ── Factory ───────────────────────────────────────────────────────────────

def get_recovery() -> Recovery:
    from . import get_engine_name
    name = get_engine_name()
    # v3 è drop-in di metis (nessun recovery_v3): in prod METNOS_ENGINE=v3 deve
    # usare MetisRecovery, non SimpleRecovery (bug 20/6 — allineato a get_proposer).
    if name in ("metis", "v3"):
        try:
            from . import recovery_metis
            return recovery_metis.MetisRecovery()
        except Exception as ex:
            log.warning("MetisRecovery unavailable (%r), fallback simple", ex)
    return SimpleRecovery()
