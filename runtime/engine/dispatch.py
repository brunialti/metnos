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
import time
from dataclasses import dataclass
from typing import Optional, Callable

from .types import Intent, Framework, RunResult
from .executor import Executor, compute_framework_hash
from .routing_pool import build_routing_pool
from . import fastpath as _fp
from . import autopath as _ap
from . import (
    is_fastpath_enabled, is_autopath_enabled, is_validator_enabled,
    is_output_policy_enabled,
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


def _maybe_record_fastpath(query: str, intent: Intent,
                            framework: Framework, run: RunResult) -> None:
    """Auto-produzione L0 (11/6/2026): un turno completato con SUCCESSO dal
    piano PIENO (engine o recovery — mai da hit L0/L1, già cache) diventa
    fastpath: alla ripetizione della stessa query il piano parte in
    millisecondi senza LLM. Le condizioni di cacheabilità (≥1 step-executor,
    no tool context-dependent, pertinenza 0a/0b) vivono in
    fastpath.record_success. Best-effort: il fallimento non blocca il turno
    ma non è silenzioso (§2.8: log)."""
    if not is_fastpath_enabled():
        return
    if run is None or run.final_kind != "answer" or run.aborted_reason:
        return
    try:
        fp_id = _fp.record_success(query, framework, intent=intent)
        if fp_id:
            log.debug("[L0 fastpath] auto-record fp_id=%d", fp_id)
    except Exception as ex:
        log.debug("fastpath.record_success fallita (best-effort): %r", ex)


def _is_get_inputs_misroute(framework: Framework) -> bool:
    """True se l'UNICO step-executor del framework (escluso final_answer) è
    get_inputs → non-decomposizione (il planner chiede invece di agire). Vedi
    guard §7.9 in run_turn. Deterministico, model-independent."""
    exec_steps = [s.tool for s in framework.steps
                  if s.tool and s.tool != "final_answer"]
    return exec_steps == ["get_inputs"]


def _dropped_required_verbs(framework: Framework, query: str, intent=None) -> set:
    """Verbi RICHIESTI dalla query ma ASSENTI dal framework → decomposizione
    incompleta. Copre PRODUCER (find/read/get/list: senza i dati la pipeline è
    monca) + side-effecting espliciti (send/create/write/move/delete/share: «manda
    mail»/«crea evento» vanno portati a termine §4.3). Es. "cerca online ... crea
    evento ... manda mail" che collassa a create_events-only (find+send droppati)
    o a find→create senza send. Universale §7.3/§7.9, multilingue (verbi canonici),
    model-indep. Conservativo: solo query MULTI-azione (≥2 verbi); i soft
    (describe/classify/sort/filter) NON sono richiesti (si fondono nel final).
    """
    try:
        from prefilter import tokenize, detect_canonical_verbs_all
        from vocab import COVERAGE_REQUIRED_VERBS, ACTIONS
    except Exception:
        return set()
    qverbs = set(detect_canonical_verbs_all(tokenize(query or "")))
    # Unisci i verbi della decomposizione LLM (intent.actions): il detector
    # lessicale non copre tutti i verbi NL ("salva"→write, "prendi"→get); la
    # decomposizione sì (multilingue, ZERO dizionari). Così la guard vede i
    # side-effecting reali della query (fix q13: clausola "salva" → write
    # droppata → describe usato come finale, nessun file scritto).
    for _a in (getattr(intent, "actions", None) or []):
        _v = _a.get("verb") if isinstance(_a, dict) else None
        if _v:
            qverbs.add(_v)
    if len(qverbs) < 2:
        return set()
    fw_verbs = set()
    for s in framework.steps:
        t = s.tool or ""
        if not t or t == "final_answer":
            continue
        head = t.split("_", 1)[0]
        if head in ACTIONS:
            fw_verbs.add(head)
    return (qverbs & set(COVERAGE_REQUIRED_VERBS)) - fw_verbs


def run_turn(*, query: str, intent: Intent, catalog: list,
              invoke_executor_cb: Callable,
              llm_call_wise: Optional[Callable] = None,
              llm_call_fast: Optional[Callable] = None,
              vaglio_judge: Optional[Callable] = None,
              remediate_args_cb: Optional[Callable] = None,
              runtime_ctx: Optional[dict] = None,
              turn_id: str = "",
              lang: str = "it",
              verbose: bool = False,
              progress=None) -> DispatchResult:
    """Entry point engine v2. Orchestrazione 4 layer.

    Returns:
      DispatchResult con final_text/kind + match_source per debug/telemetry.
    """
    t_start = time.time()
    # Pool reduction via prefilter (ADR 0164 fix): invece di passare TUTTO
    # il catalog (~80 tool, prompt 400+ righe) a Mētis, prefiltriamo per
    # intent semantic match. Top-K (default 12) coprono >90% intent canonici
    # con prompt 5-10× più piccolo → -30-40% latency Mētis.
    # La costruzione e' ESTRATTA in routing_pool.build_routing_pool (fix B3,
    # 9/6/2026): funzione PURA condivisa col guard anti-regressione
    # bench/routing_subset_bench.py, cosi' il bench esercita ESATTAMENTE il
    # pool di produzione (k da env, compound per-clausola, universal-helpers,
    # companions) e non una copia semplificata che diverge in silenzio.
    pool_names = build_routing_pool(query, intent, catalog)

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
                                remediate_args_cb=remediate_args_cb,
                                progress=progress)
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
                                remediate_args_cb=remediate_args_cb,
                                progress=progress)
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

    # Guard misroute get_inputs (§7.9, deterministico, universale): un
    # framework il cui UNICO step-executor (escluso final_answer) è get_inputs
    # è una NON-decomposizione — il planner "chiede" invece di "fare" (sweep
    # compound P3/P6/P7: comando d'azione collassato in una sola get_inputs,
    # spesso pure con dialog malformato). Una get_inputs isolata raccoglie
    # input e poi NON agisce: mai una risposta utile a un comando. Ri-propone
    # UNA volta escludendo get_inputs dal pool, forzando la scomposizione in
    # executor reali. L'uso legittimo (get_inputs SEGUITA da azione, o
    # orchestrata via needs_inputs decision) non passa di qui. Model-indep.
    # NB 9/6/2026 (causa-radice): get_inputs NON e' piu' iniettato
    # universalmente nel pool (rimosso da tool_grammar._UNIVERSAL_HELPERS) —
    # entra solo se l'intent lo giustifica (object=inputs/affinity) o col
    # full-catalog su intent incompleto. Il guard resta come DIFESA RESIDUA
    # per quei pool: nel caso comune non scatta piu' (zero re-propose).
    if _is_get_inputs_misroute(framework):
        if verbose:
            log.info("[guard] get_inputs misroute (unico step) → "
                     "re-propose senza get_inputs")
        _failed_hash = compute_framework_hash(framework)
        # exclude_tools agisce a VALLE della costruzione del pool (prompt +
        # grammar GBNF), qualunque sia la fonte che ha portato get_inputs nel
        # pool (object=inputs, affinity, full-catalog): rimuoverlo solo dal
        # pool del caller non basterebbe.
        _framework_gi = proposer.propose(
            query=query, intent=intent, pool=pool_names,
            excluded_hashes=excluded | {_failed_hash},
            llm_call=llm_call_wise, lang=lang, catalog=catalog,
            exclude_tools=("get_inputs",))
        # Solo se la ri-proposta NON è a sua volta una get_inputs-misroute
        # (difesa: re-propose potrebbe fallire o degenerare).
        if _framework_gi is not None and not _is_get_inputs_misroute(_framework_gi):
            framework = _framework_gi

    # Guard decomposizione incompleta (§7.3/§4.3, universale): query multi-azione
    # in cui il planner ha SALTATO un verbo RICHIESTO — producer (find/read/get/
    # list: senza dati la pipeline è monca) o side-effecting esplicito (send/
    # create/...: «manda mail»/«crea evento» dovuti). Es. "cerca ... crea ...
    # manda" → create-only (find+send droppati) o find→create senza send (niente
    # mail). Ri-propone UNA volta. Best-effort: se la ri-proposta è incompleta si
    # procede (esecuzione/terminator danno l'esito onesto).
    _dropped = _dropped_required_verbs(framework, query, intent)
    if _dropped:
        if verbose:
            log.info("[guard] decomposizione incompleta: verbi mancanti %s "
                     "→ re-propose", sorted(_dropped))
        _fh = compute_framework_hash(framework)
        _fw2 = proposer.propose(
            query=query, intent=intent, pool=pool_names,
            excluded_hashes=excluded | {_fh},
            llm_call=llm_call_wise, lang=lang, catalog=catalog)
        # Accetta la ri-proposta solo se copre PIÙ verbi (meno droppati).
        if _fw2 is not None and len(_dropped_required_verbs(_fw2, query, intent)) < len(_dropped):
            framework = _fw2

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

    # Output-policy deterministica (matrice intent×data_kind → modo, §7.9):
    # il runtime — non il proposer — sceglie il TERMINALE di presentazione
    # (gallery/scalar drop describe + final deterministico; web READ→T insert
    # read_urls_html). Gated METNOS_OUTPUT_POLICY=1, default OFF. SoT:
    # internal/reports/output_presentation_matrix_2026-05-31.md.
    if is_output_policy_enabled():
        try:
            from output_policy import normalize_terminal
            framework, _op_info = normalize_terminal(framework, intent, query)
            if _op_info.get("action") not in ("", "noop"):
                log.info("[output_policy] mode=%s action=%s producer-kind=%s",
                         _op_info.get("mode"), _op_info.get("action"),
                         _op_info.get("data_kind"))
        except Exception as ex:
            log.warning("output_policy normalize_terminal noop: %r", ex)

    # Execute
    run = executor.run(framework, query=query,
                        runtime_ctx=runtime_ctx,
                        remediate_args_cb=remediate_args_cb,
                        progress=progress)

    # Record observation always (per future feedback)
    if turn_id and intent.is_complete():
        try:
            _ap.record_observation(
                turn_id=turn_id, intent=intent, framework=framework,
                query=query, latency_ms=run.elapsed_ms)
        except Exception as ex:
            # Feedback best-effort: il fallimento non blocca il turno ma NON è
            # silenzioso (§2.8) — traccia per diagnosticare regressioni di
            # record_observation senza alterare il flusso.
            log.debug("record_observation fallita (best-effort): %r", ex)

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
                                     remediate_args_cb=remediate_args_cb,
                                progress=progress)
                if run2.final_kind == "answer":
                    # Il piano RECUPERATO ha funzionato: cacharlo evita di
                    # ripetere fallimento+recovery alla prossima ripetizione.
                    _maybe_record_fastpath(query, intent, framework_alt, run2)
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

    _maybe_record_fastpath(query, intent, framework, run)
    return DispatchResult(
        final_text=run.final_text, final_kind=run.final_kind,
        match_source="engine", framework_hash=run.framework_hash,
        elapsed_ms=int((time.time() - t_start) * 1000),
        run=run, framework=framework)
