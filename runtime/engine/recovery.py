"""engine/recovery.py — Protocol + SimpleRecovery (default).

Recovery interviene dopo Executor errore. Classifica error → produce
nuovo Framework via Proposer escludendo failed path.

§7.3 universalità: classifier deterministic via error_class strutturato
nel result.error_class. No regex su error_text multi-lingua.
"""
from __future__ import annotations

import logging
from typing import Optional, Callable, Protocol

from .types import Intent, Framework, RunResult, ERROR_CLASSES, RECOVERABLE
from .executor import compute_framework_hash

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
    ec = (r.get("error_class") or "").lower()
    if ec in ERROR_CLASSES:
        return ec
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
        # Esclude framework_hash del fallito
        failed_hash = failed_run.framework_hash
        excluded = {failed_hash} if failed_hash else set()
        # Esclude anche tool del last step (probabile causa)
        excluded_pool = pool
        if failed_run.steps:
            failed_tool = failed_run.steps[-1].tool
            if failed_tool:
                excluded_pool = [t for t in pool if t != failed_tool]
        try:
            return proposer.propose(
                query=query, intent=intent, pool=excluded_pool,
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
    if name == "metis":
        try:
            from . import recovery_metis
            return recovery_metis.MetisRecovery()
        except Exception as ex:
            log.warning("MetisRecovery unavailable (%r), fallback simple", ex)
    return SimpleRecovery()
