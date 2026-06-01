"""engine/dispatch.py — orchestrator dei 4 layer (entry point engine v2).

Sequence:
  1. Fastpath (L0) lookup → hit → execute direct, done.
  2. Autopath (L1) lookup → hit → execute cached framework.
  3. Validator (L2) optional → pre-execute check.
  4. Engine (L3) = Proposer → Validator (opt) → Executor → on error Recovery → on out_of_scope Terminator.

Entry point single: dispatch.run_turn(query, intent, catalog, invoke_executor_cb, ...).

§7.3 universality: il dispatcher non sa nulla di domain. Solo orchestrazione layer.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Callable

from .types import Intent, Framework, RunResult
from .executor import Executor, compute_framework_hash
from . import fastpath as _fp
from . import autopath as _ap
from . import (
    is_fastpath_enabled, is_autopath_enabled, is_validator_enabled,
)

log = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Risultato di run_turn. Sempre coerente con RunResult ma annotato
    con quale layer ha risposto."""
    final_text: str
    final_kind: str
    match_source: str  # 'fastpath' | 'autopath' | 'engine' | 'recovery' | 'terminator'
    framework_hash: str
    elapsed_ms: int
    run: Optional[RunResult] = None
    framework: Optional[Framework] = None
    error_class: str = ""


def run_turn(*, query: str, intent: Intent, catalog: list,
              invoke_executor_cb: Callable,
              llm_call_wise: Optional[Callable] = None,
              llm_call_fast: Optional[Callable] = None,
              vaglio_judge: Optional[Callable] = None,
              remediate_args_cb: Optional[Callable] = None,
              runtime_ctx: Optional[dict] = None,
              turn_id: str = "",
              lang: str = "it",
              verbose: bool = False) -> DispatchResult:
    """Entry point engine v2. Orchestrazione 4 layer.

    Returns:
      DispatchResult con final_text/kind + match_source per debug/telemetry.
    """
    t_start = time.time()
    # Pool reduction via prefilter (ADR 0164 fix): invece di passare TUTTO
    # il catalog (~80 tool, prompt 400+ righe) a Mētis, prefiltriamo per
    # intent semantic match. Top-K (default 12) coprono >90% intent canonici
    # con prompt 5-10× più piccolo → -30-40% latency Mētis.
    pool_size = int(os.environ.get("METNOS_ENGINE_POOL_SIZE", "12"))
    if intent.is_complete():
        try:
            from prefilter import rank_with_intent, rank as _rank_bow
            intent_dict = {"verb": intent.verb, "object": intent.object,
                            "keywords": intent.keywords}
            filtered = rank_with_intent(query, catalog, intent_dict, k=pool_size)
            # rank_with_intent ritorna None PER DESIGN quando il verbo intent
            # non matcha alcun executor (es. object=entries meta-oggetto, o
            # verbo intermedio di una query compound): non e' un errore, e' il
            # contratto di fallback bag-of-words (vedi prefilter.py §776). Senza
            # questo ramo il `len(pool_for_propose)` sotto crashava con
            # `len(None)` → except → full pool (80 tool) → grammar Mētis gigante
            # → wise LLM lentissimo (regressione web-search: "fondi ark" ~8min).
            if not filtered:
                filtered = _rank_bow(query, catalog, k=pool_size, min_score=0)
            # Garantisci che fastpath / autopath catalog completo resti
            # disponibile a executor (callback usa il NOME, non il pool).
            # Pool ridotto è SOLO per il prompt Proposer.
            pool_for_propose = filtered or catalog
            # §7.3: universal helpers (describe_entries/classify_entries/...)
            # sono referenziati dai PATTERN STRUTTURALI del prompt Proposer
            # (es. READ/LIST = producer + describe_entries + final_answer) ma
            # il prefilter per-verbo non li include. Senza il loro schema nel
            # pool, il Proposer inventa valori (es. style fuori enum §8.3).
            # Append idempotente dei helper presenti nel catalog.
            try:
                from tool_grammar import _UNIVERSAL_HELPERS
                present = {getattr(e, "name", None) for e in pool_for_propose}
                for ex_obj in catalog:
                    nm = getattr(ex_obj, "name", None)
                    if nm in _UNIVERSAL_HELPERS and nm not in present:
                        pool_for_propose = pool_for_propose + [ex_obj]
                        present.add(nm)
            except Exception:
                pass
            log.debug("dispatch: pool reduced %d → %d via prefilter (+helpers)",
                       len(catalog), len(pool_for_propose))
        except Exception as ex:
            log.warning("dispatch: prefilter failed (%r), full pool", ex)
            pool_for_propose = catalog
    else:
        pool_for_propose = catalog
    pool_names = [getattr(e, "name", None) for e in pool_for_propose
                   if getattr(e, "name", None)]

    executor = Executor(
        invoke_executor=invoke_executor_cb,
        llm_call_fast=llm_call_fast,
        vaglio_judge=vaglio_judge,
        catalog=catalog,
    )

    # ── Layer 0: Fastpath ────────────────────────────────────────────────
    if is_fastpath_enabled():
        fp_hit = _fp.lookup(query)
        if fp_hit is not None:
            if verbose:
                log.info("[L0 fastpath] hit (%s, sim=%.2f): %s",
                          fp_hit.match_kind, fp_hit.similarity,
                          fp_hit.canonical_text)
            run = executor.run(fp_hit.framework, query=query,
                                runtime_ctx=runtime_ctx,
                                remediate_args_cb=remediate_args_cb)
            return DispatchResult(
                final_text=run.final_text, final_kind=run.final_kind,
                match_source="fastpath", framework_hash=run.framework_hash,
                elapsed_ms=int((time.time() - t_start) * 1000),
                run=run, framework=fp_hit.framework)

    # ── Layer 1: Autopath ────────────────────────────────────────────────
    if is_autopath_enabled() and intent.is_complete():
        ap_hit = _ap.lookup(query, intent)
        if ap_hit is not None:
            if verbose:
                log.info("[L1 autopath] hit skill=%s uses=%d", ap_hit.skill_id, ap_hit.uses)
            run = executor.run(ap_hit.framework, query=query,
                                runtime_ctx=runtime_ctx,
                                remediate_args_cb=remediate_args_cb)
            # Record observation per future feedback hooks
            if turn_id and intent.is_complete():
                _ap.record_observation(
                    turn_id=turn_id, intent=intent,
                    framework=ap_hit.framework, query=query,
                    latency_ms=run.elapsed_ms)
            return DispatchResult(
                final_text=run.final_text, final_kind=run.final_kind,
                match_source="autopath", framework_hash=run.framework_hash,
                elapsed_ms=int((time.time() - t_start) * 1000),
                run=run, framework=ap_hit.framework)

    # ── Layer 3: Engine (Proposer + Executor + Recovery + Terminator) ────
    from .proposer import get_proposer
    from .recovery import get_recovery, classify_error
    from .terminator import get_terminator
    proposer = get_proposer()
    recovery = get_recovery()
    terminator = get_terminator()

    excluded = set()
    if intent.is_complete():
        excluded = _ap.excluded_framework_hashes(intent)

    framework = proposer.propose(
        query=query, intent=intent, pool=pool_names,
        excluded_hashes=excluded,
        llm_call=llm_call_wise, lang=lang, catalog=catalog)
    if framework is None:
        # Proposer failed → terminator
        resp = terminator.explain(query=query, intent=intent,
                                    failed_run=None, error_class="wrong_args")
        return DispatchResult(
            final_text=resp.final_text, final_kind="answer",
            match_source="terminator", framework_hash="",
            elapsed_ms=int((time.time() - t_start) * 1000),
            error_class="propose_failed")

    # Layer 2: Validator (opt-in)
    if is_validator_enabled():
        from .validator import Validator
        vres = Validator(catalog).check(framework)
        if not vres.ok:
            if verbose:
                log.info("[L2 validator] %d errors, requesting re-propose",
                          len(vres.errors))
            failed_hash = compute_framework_hash(framework)
            framework2 = proposer.propose(
                query=query, intent=intent, pool=pool_names,
                excluded_hashes=excluded | {failed_hash},
                llm_call=llm_call_wise, lang=lang, catalog=catalog)
            if framework2 is not None:
                framework = framework2

    # Execute
    run = executor.run(framework, query=query,
                        runtime_ctx=runtime_ctx,
                        remediate_args_cb=remediate_args_cb)

    # Record observation always (per future feedback)
    if turn_id and intent.is_complete():
        try:
            _ap.record_observation(
                turn_id=turn_id, intent=intent, framework=framework,
                query=query, latency_ms=run.elapsed_ms)
        except Exception:
            pass

    # On error → Recovery
    if run.final_kind == "error":
        err_class = classify_error(run)
        if err_class in ("wrong_tool", "wrong_args", "missing_input"):
            if verbose:
                log.info("[L3 recovery] class=%s", err_class)
            framework_alt = recovery.recover(
                failed_run=run, query=query, intent=intent,
                pool=pool_names, proposer=proposer,
                llm_call=llm_call_wise, lang=lang, catalog=catalog)
            if framework_alt is not None:
                run2 = executor.run(framework_alt, query=query,
                                     runtime_ctx=runtime_ctx,
                                     remediate_args_cb=remediate_args_cb)
                if run2.final_kind == "answer":
                    return DispatchResult(
                        final_text=run2.final_text, final_kind="answer",
                        match_source="recovery",
                        framework_hash=run2.framework_hash,
                        elapsed_ms=int((time.time() - t_start) * 1000),
                        run=run2, framework=framework_alt,
                        error_class=err_class)
        # Recovery failed or out_of_scope → Terminator
        resp = terminator.explain(
            query=query, intent=intent,
            failed_run=run, error_class=err_class)
        return DispatchResult(
            final_text=resp.final_text, final_kind="answer",
            match_source="terminator",
            framework_hash=run.framework_hash,
            elapsed_ms=int((time.time() - t_start) * 1000),
            run=run, framework=framework, error_class=err_class)

    return DispatchResult(
        final_text=run.final_text, final_kind=run.final_kind,
        match_source="engine", framework_hash=run.framework_hash,
        elapsed_ms=int((time.time() - t_start) * 1000),
        run=run, framework=framework)
