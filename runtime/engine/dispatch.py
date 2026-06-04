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


# Producer → consumer naturale da iniettare sempre nel pool (§7.3 companion).
# Un producer il cui output non è azionabile senza il consumer.
_POOL_COMPANIONS = {
    "find_urls": ["read_urls_html", "read_urls_pdf"],
}


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
    pool_size = int(os.environ.get("METNOS_ENGINE_POOL_SIZE", "12"))
    if intent.is_complete():
        try:
            from prefilter import rank_with_intent, rank as _rank_bow
            intent_dict = {"verb": intent.verb, "object": intent.object,
                            "keywords": intent.keywords}
            # Compound multi-verbo (§7.3): se la query ha >=2 verbi canonici,
            # il pool MONO-verbo di rank_with_intent escluderebbe i tool degli
            # altri sotto-intenti (es. find+write+send → "trova le issue,
            # salvale, mandami il riassunto"). Uniamo il ranking per OGNI verbo
            # canonico presente nella query cosi' il Proposer vede l'intera
            # pipeline. Bug 2/6/2026: senza unione il pool era solo find_* →
            # niente write_files/send_messages → "salva"/"manda" impossibili.
            filtered = None
            # Compound routing (4/6): PREFERISCI la decomposizione per-clausola
            # dell'intent LLM (`intent.actions` = [{verb,object}, ...]). Ogni
            # clausola rankizza il pool con il SUO object reale → i producer di
            # OGNI sotto-azione entrano nel pool. Prima il ramo rankizzava ogni
            # verbo con un UNICO `intent.object` (quello di UNA sola clausola, di
            # solito il bersaglio finale): "trova i processi ... scrivi un report"
            # → object=files per tutti → get_processes mai nel pool → Proposer
            # collassa su get_inputs (bug q20/q21 4/6). La decomposizione è
            # multilingue per costruzione (LLM) e NON usa dizionari di sinonimi.
            _pairs = []
            _acts = getattr(intent, "actions", None) or []
            if len(_acts) >= 2:
                _pairs = [((a.get("verb") or intent.verb),
                           (a.get("object") or intent.object)) for a in _acts]
            if not _pairs:
                # Fallback deterministico (LLM non ha decomposto): verbi canonici
                # rilevati nella query, con l'object PRIMARIO condiviso (storico).
                try:
                    from prefilter import (tokenize as _pf_tok,
                                            detect_canonical_verbs_all as _pf_dv)
                    _qverbs = list(dict.fromkeys(_pf_dv(_pf_tok(query))))
                except Exception:
                    _qverbs = []
                if len(_qverbs) >= 2:
                    _pairs = [(_v, intent.object) for _v in _qverbs]
            if _pairs:
                # Object-completezza per-clausola (4/6): l'LLM assegna verbi
                # ASTRATTI ("list"/"change") che spesso NON hanno un tool esatto
                # (no list_pulls, no change_issues) → rank_with_intent(verb,obj)
                # filtra per prefisso-verbo e PERDE il producer reale dell'object
                # (find_pulls_github, set_issues_github). Garantisci che TUTTI i
                # tool del catalog con QUELL'object (derivato dal NOME canonico,
                # 2° token in vocab.OBJECTS) siano candidati nel pool: il
                # Proposer (che vede le description) sceglie il verbo giusto.
                # Universale, deterministico §7.9, ZERO dizionari di sinonimi —
                # scala a nuove lingue (l'object è canonico, non NL). Fix q14
                # (pulls→find_pulls_github) + q15 (close→set_issues_github).
                try:
                    from vocab import OBJECTS as _VOBJ_SET
                    _VOBJ = set(_VOBJ_SET)
                except Exception:
                    _VOBJ = set()

                def _tool_object(_nm):
                    for _tok in (_nm or "").split("_")[1:]:
                        if _tok in _VOBJ:
                            return _tok
                    return ""

                _seen = {}
                _clause_objs = set()
                for _v, _o in _pairs:
                    if _o:
                        _clause_objs.add(_o)
                    _sub = rank_with_intent(
                        query, catalog,
                        {"verb": _v, "object": _o,
                         "keywords": intent.keywords},
                        k=pool_size) or []
                    for _e in _sub:
                        _seen[getattr(_e, "name", None)] = _e
                # Famiglia-object completa per ogni object delle clausole.
                for _e in catalog:
                    _nm = getattr(_e, "name", None)
                    if _nm and _nm not in _seen and _tool_object(_nm) in _clause_objs:
                        _seen[_nm] = _e
                if _seen:
                    filtered = list(_seen.values())
            if filtered is None:
                filtered = rank_with_intent(query, catalog, intent_dict,
                                            k=pool_size)
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
                # §7.3 COMPANION injection (universale): un producer il cui
                # output è inutile senza un CONSUMER naturale porta sempre il
                # consumer nel pool, anche se il verbo del consumer non è nella
                # query. find_urls produce URL → senza read_urls_html/pdf la
                # catena web→contenuto è monca (il proposer non può chiuderla,
                # bug ROCm 3/6). Mappa estendibile a ogni coppia simile.
                for _prod, _comps in _POOL_COMPANIONS.items():
                    if _prod in present:
                        for _c in _comps:
                            if _c in present:
                                continue
                            _co = next((e for e in catalog
                                        if getattr(e, "name", None) == _c), None)
                            if _co is not None:
                                pool_for_propose = pool_for_propose + [_co]
                                present.add(_c)
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
    if _is_get_inputs_misroute(framework):
        if verbose:
            log.info("[guard] get_inputs misroute (unico step) → "
                     "re-propose senza get_inputs")
        _failed_hash = compute_framework_hash(framework)
        # exclude_tools sopravvive alla re-iniezione degli universal helpers
        # (get_inputs è in _UNIVERSAL_HELPERS): rimuoverlo dal pool non basta,
        # il proposer lo riaggiunge. Lo escludiamo a valle (prompt + grammar).
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
                                     remediate_args_cb=remediate_args_cb,
                                progress=progress)
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
