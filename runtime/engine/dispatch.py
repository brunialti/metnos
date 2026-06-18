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
import re
import time
from dataclasses import dataclass
from typing import Optional, Callable

from .types import Intent, Framework, RunResult, StepSpec
from .executor import (
    Executor, compute_framework_hash, resolve_query_canonical_args,
)
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


def _canonical_framework_for_record(query: str, framework: Framework,
                                    catalog: Optional[list]) -> Framework:
    """Applica ai piani DA REGISTRARE la stessa ri-risoluzione degli slot
    query-specific che Executor.run applica a ESECUZIONE
    (resolve_query_canonical_args: account mail, time_window). Lo store L0
    deve riflettere cio' che esegue (§2.8): senza, un piano ereditato dal
    champion L1 veniva cachato con gli arg della query d'ORIGINE (bug live
    11/6/2026, riga «controlla tutte le mie mailbox ultime 24 ore» con
    account='metnos_system' e zero finestra) — esecuzione corretta a
    runtime, store disonesto, query_specific=0 errato (il piano con
    finestra relativa e' 0a-only per costruzione, vedi CONTENT_ARG_KEYS).
    Ritorna il framework originale se nessun arg cambia."""
    schema_map = {getattr(e, "name", None): getattr(e, "args_schema", None)
                  for e in (catalog or [])}
    changed = False
    new_steps = []
    for s in framework.steps:
        if s.tool and s.tool != "final_answer" and isinstance(s.args, dict):
            new_args = resolve_query_canonical_args(
                s.tool, dict(s.args), query,
                args_schema=schema_map.get(s.tool))
            if new_args != s.args:
                changed = True
            new_steps.append(StepSpec(
                tool=s.tool, args=new_args,
                if_prev_entries_nonempty=s.if_prev_entries_nonempty))
        else:
            new_steps.append(s)
    if not changed:
        return framework
    return Framework(steps=new_steps, fillers=framework.fillers,
                     final_message=framework.final_message)


def _should_cache_plan(framework, query) -> bool:
    """Igiene cache L0/L1 (Roberto 15/6): NON registrare un piano che bakeizza un
    VALORE NUMERICO preso dalla query (id/conteggio). Un valore baked rende L0
    non-discriminante (ri-servirebbe su query con valore diverso) e darebbe a L1
    valori baked. Bug live: delete_tasks(id=42) su «cancella task 40». Tali query
    ri-pianificano (L3) ogni volta. La GARANZIA hard è comunque a serve-time
    (`_mutating_args_grounded`); questa è prevenzione a monte. §7.9 universale.
    (Generalizzazione+re-bind dei valori in L1 = TODO Fable, fix completo.)"""
    qnums = set(re.findall(r"\d+", query or ""))
    if qnums:
        for s in (getattr(framework, "steps", []) or []):
            for k, v in (getattr(s, "args", {}) or {}).items():
                if k in ("from_step", "from_steps"):
                    continue
                if set(re.findall(r"\d+", str(v))) & qnums:
                    return False
    return True


def _mutating_args_grounded(framework, query) -> bool:
    """INVARIANTE serve-time L0/L1 (GARANZIA, Roberto 15/6). Un piano servito da
    cache (L0) o generalizzato (L1) che contiene uno step MUTANTE è eseguibile
    SOLO se ogni valore DISCRIMINANTE dei suoi arg (numero/id, slug owner/name)
    compare nella query CORRENTE. Se anche uno solo manca → quel valore viene da
    un'ALTRA query → RIFIUTA (re-plan). Invariante hard, indipendente dal
    record-side: nessuna azione distruttiva parte MAI da cache con un target che
    la query corrente non nomina (bug delete_tasks id=42 su «cancella task 40»).
    §7.9. I read non sono toccati (zero impatto su latenza)."""
    try:
        from pipeline_effects import MUTATING_TOOL_PREFIXES
    except Exception:
        MUTATING_TOOL_PREFIXES = ("delete_", "move_", "send_", "write_",
                                  "set_", "create_", "change_", "share_")
    qn = (query or "").lower()
    for s in (getattr(framework, "steps", []) or []):
        tool = getattr(s, "tool", "") or ""
        if not any(tool.startswith(p) for p in MUTATING_TOOL_PREFIXES):
            continue
        for k, v in (getattr(s, "args", {}) or {}).items():
            if k in ("from_step", "from_steps"):
                continue
            for tok in re.findall(r"\d+|[a-z0-9._-]+/[a-z0-9._-]+", str(v).lower()):
                if tok not in qn:
                    return False
    return True


def _maybe_record_fastpath(query: str, intent: Intent,
                            framework: Framework, run: RunResult,
                            origin: str = "auto",
                            catalog: Optional[list] = None) -> None:
    """Auto-produzione L0 (11/6/2026; classe estesa 12/6/2026): un turno
    completato con SUCCESSO da un piano la cui query esatta NON è ancora in
    cache 0a diventa fastpath: alla ripetizione della stessa query il piano
    parte in millisecondi senza LLM né scan. Sorgenti (origin):
      - 'auto'     — piano PIENO (engine, anche dopo recovery riuscita);
      - 'autopath' — hit L1: il piano di cluster vale anche per la query
        esatta. Bug live 11/6/2026: «controlla tutte le mie mailbox ultime
        24 ore» non registrava MAI perché la famiglia read|messages aveva
        già una skill L1 → ogni ripetizione ripagava embed+scan L1 invece
        del lookup hash 0a;
      - 'cosine'   — hit 0b: il piano servito appartiene a un'ALTRA query
        canonica; registrarlo sotto l'hash di QUESTA promuove la prossima
        ripetizione identica a 0a (niente scan O(N)).
    MAI da hit 0a: la riga esiste già (lookup._touch ne traccia l'uso).
    Le condizioni di cacheabilità (≥1 step-executor, no tool
    context-dependent, no literal temporale assoluto, pertinenza 0a/0b)
    vivono in fastpath.record_success. Best-effort: il fallimento non blocca
    il turno ma non è silenzioso (§2.8: log).

    Criterio di EFFICACIA (12/6/2026, bug live 1dcc8307): `final_kind=answer`
    NON basta — un piano il cui step MUTANTE (delete/move/send/...) ha avuto
    0 effetto reale (n_*=0 / ok=False; es. delete_credentials «not found»)
    è un piano «ok ma a vuoto»: cacharlo lo auto-perpetua e ri-serve il
    misroute in millisecondi bypassando il proposer. Confine deterministico
    §7.9 in pipeline_effects.ineffective_mutations: SOLO i mutanti eseguiti
    a 0-effetto bloccano; un producer (find/read/list) a 0 risultati è un
    esito VALIDO cacheabile; un mutante saltato dalla guard condizionale o
    senza output contabile non è giudicabile e non blocca. Costo del falso
    positivo (mutante legittimamente a vuoto, es. «sposta lo spam» con 0
    spam): il piano si cacherà alla prima esecuzione CON effetto — un
    re-planning in più, mai un misroute perpetuato."""
    if not is_fastpath_enabled():
        return
    if run is None or run.final_kind != "answer" or run.aborted_reason:
        return
    # Cacheabilità L0 (Roberto 15/6): solo pipeline multi-step che NON bakeizzano
    # un valore numerico della query (vedi _should_cache_plan). Esclude il bug
    # delete_tasks(id=42) ri-servito su «cancella task 40».
    if not _should_cache_plan(framework, query):
        return
    try:
        from pipeline_effects import ineffective_mutations
        ineff = ineffective_mutations(run.steps)
    except Exception as ex:  # best-effort ma non silenzioso (§2.8)
        log.warning("fastpath efficacy-check fallito (registro comunque): %r", ex)
        ineff = []
    if ineff:
        log.info("[L0 fastpath] skip record: step mutante a 0 effetto reale "
                 "%s — piano 'ok a vuoto' non cacheabile (criterio efficacia)",
                 ineff)
        return
    # Efficacia estesa (16/6, turn e591854e/71117eef): un piano che DICHIARA
    # un mutante NON-guardato (senza if_prev_entries_nonempty) ma il turno ha
    # 0 effetto reale (0 items E 0 mutations — il mutante e' stato auto-skippato
    # su input vuoto da _mutating_input_is_empty, quindi assente da run.steps e
    # invisibile a ineffective_mutations) NON va cachato: cacharlo auto-perpetua
    # il misroute «no-location→files→0→skip→cache→ri-serve». §7.9.
    #   Confine (vs test_skipped_conditional_mutant_not_blocked): un mutante
    # con if_prev_entries_nonempty=True e' GUARDATO — il piano anticipa la
    # vuotezza come esito NORMALE («svuota lo spam» con 0 spam = piano corretto,
    # vuoto oggi) → cacheabile. Solo il mutante NON-guardato a 0-effetto e'
    # sintomo di piano malformato/misroutato → bloccato. Prima dell'auto-skip
    # questo mutante eseguiva con entries=[] e ineffective_mutations lo coglieva
    # (riga entries==[]); l'auto-skip ha spostato qui quel confine.
    try:
        from pipeline_effects import (pipeline_effect_counts,
                                       MUTATING_TOOL_PREFIXES)
        _c = pipeline_effect_counts(run.steps)
        _declared_unguarded_mutant = any(
            any((getattr(s, "tool", "") or "").startswith(p)
                for p in MUTATING_TOOL_PREFIXES)
            and not getattr(s, "if_prev_entries_nonempty", False)
            for s in (framework.steps or []))
        if (_c and _c.get("items", 0) == 0 and _c.get("mutations", 0) == 0
                and _declared_unguarded_mutant):
            log.info("[L0 fastpath] skip record: mutante non-guardato dichiarato "
                     "ma 0 effetto reale (0 items/0 mutations) — piano inefficace "
                     "non cacheabile")
            return
    except Exception as ex:
        log.warning("fastpath noop-check fallito (registro comunque): %r", ex)
    try:
        framework = _canonical_framework_for_record(query, framework, catalog)
        fp_id = _fp.record_success(query, framework, intent=intent,
                                   origin=origin)
        if fp_id:
            log.info("[L0 fastpath] auto-record fp_id=%d (origin=%s)",
                     fp_id, origin)
    except Exception as ex:
        # WARNING, non debug (§2.8): a livello debug questo ramo era
        # invisibile in prod (INFO) e ha nascosto per giorni la causa-radice
        # delle 0 righe (IntegrityError approved_at, fix 11/6/2026).
        log.warning("fastpath.record_success fallita (best-effort): %r", ex)


def _apply_ordering_clause(framework: Framework, query: str,
                           catalog: Optional[list]) -> Framework:
    """Normalizzazione deterministica «ordina/raggruppa per X» (§7.9,
    bug live 12/6/2026 T38/T39): qualunque layer abbia prodotto il piano
    (fastpath/autopath/engine/recovery), la clausola di ordinamento della
    query CORRENTE viene tradotta in uno step `sort_entries(by=X)` +
    `group_by=X` sul describe terminale — l'output riflette la chiave
    richiesta invece del raggruppamento intrinseco per tema. Applicata nel
    funnel di dispatch (non nel proposer): un piano cachato/ereditato resta
    un template di STRUTTURA, la clausola si ri-deriva dalla query a ogni
    esecuzione (stessa filosofia di resolve_query_canonical_args).
    Idempotente, no-op senza clausola. Best-effort: mai blocca il turno."""
    try:
        from ordering_clause import apply_to_framework
        names = {getattr(e, "name", None) for e in (catalog or [])}
        names.discard(None)
        normalized = apply_to_framework(framework, query,
                                        catalog_names=names or None)
        if normalized is not framework:
            log.info("[ordering_clause] piano normalizzato: %s",
                     [s.tool for s in normalized.steps])
        return normalized
    except Exception as ex:
        log.warning("ordering_clause noop (best-effort): %r", ex)
        return framework


def _enforce_missing_clauses(framework: Framework, intent, query: str,
                             catalog: Optional[list]) -> Framework:
    """§7.9 fallback DETERMINISTICO (Roberto 17/6): se dopo skeleton-hint +
    re-propose una clausola RICHIESTA di `intent.actions` resta SCOPERTA, APPENDI
    lo step mancante (tool object-aligned dal catalog + `from_step` all'ultimo
    step-dati + args derivabili dalla query: `store` da «store <X>»). Inserito
    PRIMA di final_answer, in ordine intent.actions. È l'ENFORCEMENT (ultima
    risorsa) dopo che il proposer NON-VINCOLANTE non ha coperto la clausola:
    garantisce la STRUTTURA; gli args semantici fini (es. status) restano
    dell'LLM/entries. Conservativo: appende SOLO se deriva un tool reale; mai
    inventa tool. No-op senza clausole scoperte. Best-effort."""
    try:
        still = _dropped_required_verbs(framework, query, intent)
        if not still:
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        if not steps:
            return framework
        names = {getattr(e, "name", None) if not isinstance(e, dict)
                 else e.get("name") for e in (catalog or [])}
        names.discard(None)
        from compound_decomposer import derive_tool_name
        import re as _re
        from .types import StepSpec
        from . import is_v3
        # GAP-B residue (redesign): in v3 il derive delle clausole SCOPERTE e'
        # provider-aware → un compound github che enforce-a la clausola send
        # appende send_messages_github, NON il generico (P1 gatea il pool ma
        # l'enforce ricostruiva il tool col derive provider-blind). v2: query=None
        # → comportamento invariato.
        _q = query if is_v3() else None
        # from_step = ultimo step-executor (1-based) prima del final_answer
        exec_n = sum(1 for s in steps if (s.tool or "") != "final_answer")
        m_store = _re.search(r"\bstore\s+([A-Za-z0-9_]+)", query or "")
        store = m_store.group(1) if m_store else None
        new_steps: list = []
        for a in (getattr(intent, "actions", None) or []):
            v = a.get("verb") if isinstance(a, dict) else None
            o = a.get("object") if isinstance(a, dict) else None
            if not v or v not in still:
                continue
            tool = derive_tool_name(v, o, names, query=_q)
            if not tool:
                continue
            args: dict = {}
            if exec_n >= 1:
                args["from_step"] = exec_n
            if o == "entries" and store:
                args["store"] = store
            new_steps.append(StepSpec(tool=tool, args=args))
            still.discard(v)
            exec_n += 1  # i nuovi step si concatenano
        if not new_steps:
            return framework
        # inserisci prima di final_answer (se presente), altrimenti in coda
        out = [s for s in steps if (s.tool or "") != "final_answer"]
        out.extend(new_steps)
        finals = [s for s in steps if (s.tool or "") == "final_answer"]
        out.extend(finals or [])
        framework.steps = out
        log.info("[enforce_clauses] appended %d step(s) per clausole scoperte: %s",
                 len(new_steps), [s.tool for s in new_steps])
        return framework
    except Exception as ex:
        log.warning("enforce_missing_clauses noop (best-effort): %r", ex)
        return framework


def _align_framework_objects(framework: Framework, intent,
                             catalog: Optional[list]) -> Framework:
    """§7.9 deterministico: ri-allinea il tool di uno step quando il proposer
    ha scelto un FRATELLO con l'OGGETTO SBAGLIATO che l'intent NON ha chiesto.

    Bug live 17/6/2026: clausola intent {find,issues} ma Metis compone
    `find_pulls_github` (object=pulls) benche' il prefilter ranki
    `find_issues_github` #1 (prefilter giusto, LLM sbaglia il fratello). Causa
    radice del misroute compound github (issue->pull). Generale (qualunque
    verbo/oggetto): scatta SOLO se l'oggetto scelto e' ASSENTE fra gli oggetti
    che l'intent ha decomposto per quel verbo E un oggetto-intent per quel
    verbo mappa a un tool reale del catalog (stesso qualifier/provider) ->
    swap. Basso falso-positivo: richiede che l'intent dissenta ESPLICITAMENTE.
    No-op senza intent.actions o se l'oggetto gia' combacia. Best-effort."""
    try:
        actions = getattr(intent, "actions", None) or []
        steps = getattr(framework, "steps", None) or []
        if not actions or not steps:
            return framework
        by_verb: dict = {}
        for a in actions:
            v = a.get("verb") if isinstance(a, dict) else None
            o = a.get("object") if isinstance(a, dict) else None
            if v and o:
                by_verb.setdefault(v, [])
                if o not in by_verb[v]:
                    by_verb[v].append(o)
        if not by_verb:
            return framework
        names = {getattr(e, "name", None) if not isinstance(e, dict)
                 else e.get("name") for e in (catalog or [])}
        names.discard(None)
        import naming_grammar as _ng
        changed = False
        for st in steps:
            tool = getattr(st, "tool", None)
            nc = _ng.parse_name(tool) if tool else None
            if not nc:
                continue
            intent_objs = by_verb.get(nc.verb)
            if not intent_objs or nc.obj in intent_objs:
                continue  # verbo non decomposto, o oggetto gia' corretto
            for o2 in intent_objs:
                cand = "_".join([nc.verb, o2]
                                + ([nc.qualifier] if nc.qualifier else []))
                if cand in names and cand != tool:
                    st.tool = cand
                    changed = True
                    break
                cand2 = f"{nc.verb}_{o2}"
                if cand2 in names and cand2 != tool:
                    st.tool = cand2
                    changed = True
                    break
        if changed:
            log.info("[align_objects] tool ri-allineati all'intent: %s",
                     [s.tool for s in steps])
        return framework
    except Exception as ex:
        log.warning("align_objects noop (best-effort): %r", ex)
        return framework


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


def _normalize_store_clauses(intent, query: str, catalog: Optional[list]) -> None:
    """D2-c (§7.9, 18/6): se la query referenzia uno STORE-SINK interno
    (detection_lexicon `object.store_sink`), ri-mappa a `entries` le clausole
    di `intent.actions` che l'LLM ha classificato con un OGGETTO NON-routabile.

    Lo store e' un contenitore `entries`: write/delete/find su uno store sono
    write_entries/delete_entries/find_entries. Sotto contesa del server
    condiviso l'LLM puo' flippare la clausola store a {write, issues}
    (derive_tool_name=None → enforce affamato, vedi `_enforce_missing_clauses`).

    Sicuro per costruzione (tool-existence guard): flippa SOLO se (verb,object)
    NON risolve un tool reale MA (verb,entries) si'. Cosi' la clausola
    {find,issues}→find_issues_github (routabile) NON viene mai toccata, e solo
    le clausole store orfane (write_issues inesistente) diventano write_entries.
    Muta `intent.actions` in place. No-op senza actions o store-sink. Gira
    PRIMA della cache: la sig compound-aware vede le actions gia' corrette."""
    try:
        actions = getattr(intent, "actions", None) or []
        if not actions:
            return
        import detection_lexicon as _dl
        if not _dl.match("object.store_sink", query or ""):
            return
        names = {getattr(e, "name", None) if not isinstance(e, dict)
                 else e.get("name") for e in (catalog or [])}
        names.discard(None)
        from compound_decomposer import derive_tool_name
        # Verbi store-capaci: le tre operazioni dello store generico
        # (find/write/delete_entries). `read`/`get` su store → find_entries.
        _STORE_VERBS = {"write", "delete", "find", "read", "get"}
        changed = False
        for a in actions:
            if not isinstance(a, dict):
                continue
            v = (a.get("verb") or "").lower()
            o = (a.get("object") or "").lower()
            if v not in _STORE_VERBS or o == "entries":
                continue
            if derive_tool_name(v, o, names) is None and \
                    derive_tool_name(v, "entries", names):
                a["object"] = "entries"
                changed = True
        if changed:
            log.info("[store_clauses] ri-mappate a entries: %s",
                     [(a.get("verb"), a.get("object")) for a in actions])
    except Exception as ex:
        log.warning("normalize_store_clauses noop (best-effort): %r", ex)


# ${stepN.field} ref (P2 reorder: rimappa N dopo il riordino degli step).
_STEPREF_RE = re.compile(r"(\$\{step)(\d+)")


def _remap_step_refs(obj, idx_map: dict):
    """Riscrive from_step:int e ${stepN...} secondo idx_map (old→new 1-based).
    Ricorsivo su dict/list/str; ritorna NUOVE strutture (non muta l'input).
    Identita' per indici non in idx_map. §7.9 deterministico."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "from_step" and isinstance(v, int):
                out[k] = idx_map.get(v, v)
            else:
                out[k] = _remap_step_refs(v, idx_map)
        return out
    if isinstance(obj, list):
        return [_remap_step_refs(x, idx_map) for x in obj]
    if isinstance(obj, str):
        return _STEPREF_RE.sub(
            lambda m: m.group(1) + str(idx_map.get(int(m.group(2)), int(m.group(2)))),
            obj)
    return obj


def _conform_to_intent_order(framework: Framework, intent, query: str,
                             catalog: Optional[list]) -> Framework:
    """§7.9 v3 (GAP-C ordine, redesign): riordina gli step-executor nell'ORDINE
    di intent.actions (autoritativo, ordinato dall'extractor) e rimappa from_step
    + ${stepN.field}. Risolve l'ordine sbagliato quando proposer/enforce emette/
    appende step in sequenza diversa dalla query — es. FASE 3: enforce appende
    send_messages_github DOPO write_entries → reorder a find→send→write. Gated
    is_v3() dal caller (v2 invariato).

    Robusto agli helper trasformativi (filter/sort/describe/...): NON partecipano
    al match con le action (sono SOFT) e seguono il loro producer (rank = quello
    dello step HARD precedente). Conservativo: NO-OP se uno step HARD (producer/
    mutating) non matcha alcuna action distinta, o se il riordino creerebbe un
    from_step in avanti (consumer prima del producer). No-op su mono-azione o
    piano gia' in ordine. final_answer resta in coda."""
    try:
        actions = [a for a in (getattr(intent, "actions", None) or [])
                   if isinstance(a, dict)]
        if len(actions) < 2:
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        execs = [s for s in steps if (s.tool or "") != "final_answer"]
        finals = [s for s in steps if (s.tool or "") == "final_answer"]
        if len(execs) < 2:
            return framework
        import naming_grammar as _ng
        try:
            from compound_decomposer import (PRODUCER_VERBS as _PROD,
                                             TRANSFORM_VERBS as _SOFT)
        except Exception:
            _PROD = {"find", "read", "get", "list"}
            _SOFT = {"filter", "sort", "group", "classify", "describe",
                     "render", "compute", "compare"}
        # Pass 1: match HARD step (producer/mutating) → action (greedy best-score:
        # object 2 + verbo-esatto 2 | verbo-famiglia 1). SOFT (transform) saltati.
        remaining = list(enumerate(actions))
        hard_rank: dict = {}
        for st in execs:
            nc = _ng.parse_name(st.tool or "")
            sv = (nc.verb if nc else "") or ""
            so = (nc.obj if nc else "") or ""
            if sv in _SOFT:
                continue  # helper trasformativo: SOFT, non matcha action
            # Match step→action OBJECT-aware (bugfix multi-dominio): per le
            # action PRODUCER l'OGGETTO deve combaciare (un read_messages NON
            # puo' rubare lo slot di find_events solo perche' entrambi
            # produttori) — senza questo, un duplicato del proposer consumava
            # l'action di un altro dominio e il vero produttore floatava in coda
            # (ordine sbagliato). Per le action CONSUMER basta il verbo (object
            # generico: compress_files copre 'comprimi le foto'). Primo match in
            # ordine intent.
            best_j = None
            for j, (ai, a) in enumerate(remaining):
                av = (a.get("verb") or "").lower()
                ao = (a.get("object") or "").lower()
                if sv in _PROD:
                    # PRODUCER step → action PRODUCER o TRANSFORM con lo stesso
                    # OGGETTO: il produttore serve la clausola che usa quell'object
                    # anche se l'intent l'ha etichettata filter/sort/group (es.
                    # find_entries copre la clausola (filter,entries) di «trova le
                    # spese sopra 100»). NON una action mutating (quella ha il suo
                    # step). Object obbligatorio: find_events non ruba find_images.
                    ok = bool(so and so == ao and (av in _PROD or av in _SOFT))
                else:
                    # MUTATOR step → action con lo stesso VERBO (object generico).
                    ok = bool(sv and sv == av)
                if ok:
                    best_j = j
                    break
            if best_j is None:
                # step HARD non mappabile (es. duplicato dal proposer): NON
                # abortire — FLOAT (rank del producer HARD precedente). I
                # duplicati non bloccano il riordino. Safety = forward-dep check.
                continue
            ai, _a = remaining.pop(best_j)
            hard_rank[id(st)] = ai
        # Pass 2: rank di ogni exec (SOFT eredita il producer HARD precedente).
        ranks: dict = {}
        last = -0.5
        for st in execs:
            if id(st) in hard_rank:
                last = float(hard_rank[id(st)])
                ranks[id(st)] = last
            else:
                ranks[id(st)] = last + 0.5
        order_pairs = sorted(enumerate(execs),
                             key=lambda kv: (ranks[id(kv[1])], kv[0]))
        sorted_execs = [execs[i] for i, _ in order_pairs]
        if sorted_execs == execs:
            return framework  # gia' in ordine
        new_list = sorted_execs + finals
        old_pos = {id(s): i + 1 for i, s in enumerate(steps)}
        new_pos = {id(s): i + 1 for i, s in enumerate(new_list)}
        idx_map = {old_pos[id(s)]: new_pos[id(s)] for s in steps}
        # Rimappa su COPIE; valida forward-ref; commit solo se valido.
        remapped = [(s, _remap_step_refs(s.args, idx_map)) for s in new_list]
        for i, (s, ra) in enumerate(remapped):
            fs = ra.get("from_step") if isinstance(ra, dict) else None
            if isinstance(fs, int) and fs > i + 1:
                return framework  # consumer prima del producer → abort
        for s, ra in remapped:
            s.args = ra
        framework.final_message = _remap_step_refs(
            getattr(framework, "final_message", "") or "", idx_map)
        framework.steps = new_list
        log.info("[conform_order] step riordinati su intent.actions: %s",
                 [s.tool for s in new_list])
        return framework
    except Exception as ex:
        log.warning("conform_to_intent_order noop (best-effort): %r", ex)
        return framework


def _enforce_missing_objects(framework: Framework, intent, query: str,
                             catalog: Optional[list]) -> Framework:
    """§7.9 v3 (drop multi-dominio): per ogni clausola PRODUCER (find/read/get/
    list, object) di intent.actions, garantisce un PRODUTTORE di quell'object nel
    piano. Il guard verb-level (`_enforce_missing_clauses`) NON vede i drop
    per-object: con N domini che condividono il verbo `find`, un `find_images`
    droppato resta nascosto (`find` risulta coperto da un altro dominio) →
    produttore-dominio perso silenziosamente (causa-radice del limite #domini).

    Appende i produttori-object MANCANTI come step INDIPENDENTI (no from_step:
    sono ricerche distinte, non pipe). `_conform_to_intent_order` li riordina poi
    nella posizione di intent.actions. intent.actions e' COMPLETO anche a 6-7
    domini (verificato) → il segnale e' affidabile. No-op su mono-azione."""
    try:
        actions = [a for a in (getattr(intent, "actions", None) or [])
                   if isinstance(a, dict)]
        if len(actions) < 2:
            return framework
        from compound_decomposer import (derive_tool_name, PRODUCER_VERBS,
                                         TRANSFORM_VERBS)
        import naming_grammar as _ng
        names = {getattr(e, "name", None) if not isinstance(e, dict)
                 else e.get("name") for e in (catalog or [])}
        names.discard(None)
        steps = list(getattr(framework, "steps", None) or [])
        produced = set()
        for s in steps:
            nc = _ng.parse_name(s.tool or "")
            if nc and nc.verb in PRODUCER_VERBS and nc.obj:
                produced.add(nc.obj)
        # Oggetti che RICHIEDONO un producer: clausole PRODUCER (col loro verbo) +
        # TRANSFORM (filter/sort/group/classify su O → §2.2 «TRANSFORMER RICHIEDE
        # PRODUCER»: produci O prima). Senza, un intent come «trova le spese sopra
        # 100» → (filter,entries) lascia entries senza produttore → drop. Dedup
        # per object, ordine intent.
        need = []
        seen_need = set()
        for a in actions:
            v = (a.get("verb") or "").lower()
            o = (a.get("object") or "").lower()
            if not o or o in seen_need:
                continue
            if v in PRODUCER_VERBS:
                need.append((v, o)); seen_need.add(o)
            elif v in TRANSFORM_VERBS:
                need.append(("find", o)); seen_need.add(o)
        new_steps: list = []
        added = set()
        for v, o in need:
            if o in produced or o in added:
                continue
            tool = derive_tool_name(v, o, names, query=query)
            if not tool:   # fallback: qualunque producer dell'object
                for pv in ("find", "read", "get", "list"):
                    if pv == v:
                        continue
                    tool = derive_tool_name(pv, o, names, query=query)
                    if tool:
                        break
            if not tool:
                continue
            new_steps.append(StepSpec(tool=tool, args={}))
            added.add(o)
        if not new_steps:
            return framework
        out = [s for s in steps if (s.tool or "") != "final_answer"]
        out.extend(new_steps)
        finals = [s for s in steps if (s.tool or "") == "final_answer"]
        out.extend(finals or [])
        framework.steps = out
        log.info("[enforce_objects] aggiunti produttori-object mancanti: %s",
                 [s.tool for s in new_steps])
        return framework
    except Exception as ex:
        log.warning("enforce_missing_objects noop (best-effort): %r", ex)
        return framework


def _apply_deterministic_structure_guards(framework: Framework, intent,
                                          query: str,
                                          catalog: Optional[list]) -> Framework:
    """Guard DETERMINISTICI di struttura (no LLM, idempotenti), condivisi da
    L0/L1 (hit cache) e L3 (proposer): `_align_framework_objects` ri-allinea i
    tool-fratelli all'oggetto dell'intent; `_enforce_missing_clauses` appende le
    clausole RICHIESTE scoperte. No-op su query mono-azione (entrambi
    richiedono `intent.actions`). NON include il re-propose LLM dei dropped:
    quello resta L3-only.

    v3: dopo align+enforce(verb-level), `_enforce_missing_objects` ripristina i
    produttori-object droppati (multi-dominio), poi `_conform_to_intent_order`
    riordina su intent.actions (gated is_v3() → v2 byte-invariato)."""
    framework = _align_framework_objects(framework, intent, catalog)
    framework = _enforce_missing_clauses(framework, intent, query, catalog)
    from . import is_v3
    if is_v3():
        framework = _enforce_missing_objects(framework, intent, query, catalog)
        framework = _conform_to_intent_order(framework, intent, query, catalog)
    return framework


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
    # D2-c (18/6): normalizza le clausole store dell'intent (store-sink →
    # entries) PRIMA di pool/cache/proposer, cosi' la sig compound-aware e il
    # routing vedono le actions gia' corrette. Deterministico, no-op se non
    # compound o senza store-sink.
    _normalize_store_clauses(intent, query, catalog)
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
            # Morte C1 a hit-time (§2.8): un piano che riferisce un executor
            # non più nel catalog (ritirato/rinominato/archiviato) NON va
            # eseguito (fallirebbe wrong_tool) né tenuto: delete +
            # fall-through a L1/L3, che ripianificano col catalog corrente;
            # il successo ri-crea il fastpath col piano nuovo (self-healing).
            _cat_names = {getattr(e, "name", None) for e in catalog}
            _missing = [s.tool for s in fp_hit.framework.steps
                        if s.tool and s.tool != "final_answer"
                        and s.tool not in _cat_names]
            if _cat_names and _missing:
                log.info("[L0 fastpath] fp_id=%d riferisce executor mancanti "
                         "%s → morte + fall-through", fp_hit.fp_id, _missing)
                _fp.delete(fp_hit.fp_id)
                fp_hit = None
        # GARANZIA (Roberto 15/6): mai eseguire un piano L0 con step mutante i
        # cui valori-arg discriminanti non sono nella query corrente (re-plan).
        if fp_hit is not None and not _mutating_args_grounded(fp_hit.framework, query):
            log.info("[L0 fastpath] REJECT mis-serve: step mutante con valore "
                     "non presente nella query → fall-through/re-plan")
            fp_hit = None
        if fp_hit is not None:
            if verbose:
                log.info("[L0 fastpath] hit (%s, sim=%.2f): %s",
                          fp_hit.match_kind, fp_hit.similarity,
                          fp_hit.canonical_text)
            # Clausola «ordina/raggruppa per X» della query CORRENTE: il
            # piano cachato è un template — la clausola si ri-applica a
            # ogni esecuzione (T39 12/6/2026: il piano memoizzato ignorava
            # «ordinate per mailbox»; self-healing senza invalidare la riga).
            fp_hit.framework = _apply_ordering_clause(
                fp_hit.framework, query, catalog)
            # D3-B (18/6): i guard deterministici (align/enforce) girano anche
            # sugli HIT cache — un piano L0 compound stale/read-only (clausola
            # write droppata) verrebbe altrimenti eseguito BYPASSANDO i correttori
            # (finora path L3-only). Idempotente + no-op su mono. No LLM (il
            # re-propose dei dropped resta L3). Self-healing: _maybe_record_fastpath
            # registra il piano corretto.
            fp_hit.framework = _apply_deterministic_structure_guards(
                fp_hit.framework, intent, query, catalog)
            run = executor.run(fp_hit.framework, query=query,
                                runtime_ctx=runtime_ctx,
                                remediate_args_cb=remediate_args_cb,
                                progress=progress)
            # Promozione 0b→0a (classe 12/6/2026): il piano è arrivato via
            # cosine da un'ALTRA query canonica → registra l'hash di QUESTA
            # (vedi _maybe_record_fastpath). L'hit 0a NON registra: la riga
            # esiste già.
            if fp_hit.match_kind == "cosine":
                _maybe_record_fastpath(query, intent, fp_hit.framework, run,
                                       origin="cosine", catalog=catalog)
            return DispatchResult(
                final_text=run.final_text, final_kind=run.final_kind,
                match_source="fastpath", framework_hash=run.framework_hash,
                elapsed_ms=int((time.time() - t_start) * 1000),
                run=run, framework=fp_hit.framework)

    # ── Layer 1: Autopath ────────────────────────────────────────────────
    if is_autopath_enabled() and intent.is_complete():
        ap_hit = _ap.lookup(query, intent)
        # GARANZIA (Roberto 15/6): stessa invariante di L0 — un piano L1 con step
        # mutante i cui valori-arg non sono nella query corrente NON va eseguito
        # (un autopath con valore baked servirebbe il target sbagliato).
        if ap_hit is not None and not _mutating_args_grounded(ap_hit.framework, query):
            log.info("[L1 autopath] REJECT mis-serve: step mutante con valore "
                     "non presente nella query → fall-through a L3 (re-plan)")
            ap_hit = None
        if ap_hit is not None:
            if verbose:
                log.info("[L1 autopath] hit autopath=%s uses=%d", ap_hit.autopath_id, ap_hit.uses)
            # Clausola di ordinamento della query corrente (vedi sopra):
            # la skill di cluster è un template, la clausola NON vi è
            # incorporata (causa-radice T39: l'hit L1 della famiglia
            # read|messages ignorava «ordinate per mailbox»).
            ap_hit.framework = _apply_ordering_clause(
                ap_hit.framework, query, catalog)
            # D3-B (18/6): stessa difesa di L0 — i guard deterministici girano
            # anche sull'hit L1 (autopath generalizzato) prima dell'execute.
            ap_hit.framework = _apply_deterministic_structure_guards(
                ap_hit.framework, intent, query, catalog)
            run = executor.run(ap_hit.framework, query=query,
                                runtime_ctx=runtime_ctx,
                                remediate_args_cb=remediate_args_cb,
                                progress=progress)
            # Record observation per future feedback hooks. Skip se il piano non
            # è cacheabile (single-executor / valore numerico baked dalla query):
            # L1 non deve avere valori baked (Roberto 15/6).
            if (turn_id and intent.is_complete()
                    and _should_cache_plan(ap_hit.framework, query)):
                _ap.record_observation(
                    turn_id=turn_id, intent=intent,
                    framework=ap_hit.framework, query=query,
                    latency_ms=run.elapsed_ms)
            # Copertura L0 (bug live 11/6/2026, classe 12/6/2026): un hit L1
            # è un TURNO-SUCCESSO la cui query esatta non è in cache 0a —
            # senza record la stessa query ripaga PER SEMPRE embed+scan L1
            # e il fastpath non si auto-produce mai per le query la cui
            # famiglia ha già una skill (vedi _maybe_record_fastpath).
            _maybe_record_fastpath(query, intent, ap_hit.framework, run,
                                   origin="autopath", catalog=catalog)
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
        # (1) Re-propose RINFORZATO: la skeleton diventa vincolante per le
        # clausole droppate (`intent._repropose_cover`) — l'LLM le include con
        # gli args giusti (legge la query). Attr transitorio, ripulito dopo.
        _cover = [a for a in (getattr(intent, "actions", None) or [])
                  if isinstance(a, dict) and a.get("verb") in _dropped]
        try:
            setattr(intent, "_repropose_cover", _cover)
            _fw2 = proposer.propose(
                query=query, intent=intent, pool=pool_names,
                excluded_hashes=excluded | {_fh},
                llm_call=llm_call_wise, lang=lang, catalog=catalog)
        finally:
            try:
                delattr(intent, "_repropose_cover")
            except Exception:
                pass
        # Accetta la ri-proposta solo se copre PIÙ verbi (meno droppati).
        if _fw2 is not None and len(_dropped_required_verbs(_fw2, query, intent)) < len(_dropped):
            framework = _fw2

    # Guard DETERMINISTICI di struttura (§7.9): (1) align — ri-allinea i
    # tool-fratelli con object NON richiesto (es. find_pulls_github per clausola
    # {find,issues}); (2) enforce — appende le clausole RICHIESTE scoperte dopo
    # skeleton+re-propose. Stessa sequenza condivisa dagli hit cache L0/L1 (D3-B).
    framework = _apply_deterministic_structure_guards(
        framework, intent, query, catalog)

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

    # Clausola «ordina/raggruppa per X» (§7.9): garantita a valle del
    # proposer — l'LLM non è tenuto a tradurla, la traduzione è codice.
    framework = _apply_ordering_clause(framework, query, catalog)

    # Execute
    run = executor.run(framework, query=query,
                        runtime_ctx=runtime_ctx,
                        remediate_args_cb=remediate_args_cb,
                        progress=progress)

    # Record observation (per future feedback). Skip se non cacheabile
    # (single-executor / valore numerico baked): L1 non deve avere valori baked.
    if (turn_id and intent.is_complete()
            and _should_cache_plan(framework, query)):
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
                framework_alt = _apply_ordering_clause(
                    framework_alt, query, catalog)
                run2 = executor.run(framework_alt, query=query,
                                     runtime_ctx=runtime_ctx,
                                     remediate_args_cb=remediate_args_cb,
                                progress=progress)
                if run2.final_kind == "answer":
                    # Il piano RECUPERATO ha funzionato: cacharlo evita di
                    # ripetere fallimento+recovery alla prossima ripetizione.
                    _maybe_record_fastpath(query, intent, framework_alt, run2,
                                           catalog=catalog)
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

    _maybe_record_fastpath(query, intent, framework, run, catalog=catalog)
    return DispatchResult(
        final_text=run.final_text, final_kind=run.final_kind,
        match_source="engine", framework_hash=run.framework_hash,
        elapsed_ms=int((time.time() - t_start) * 1000),
        run=run, framework=framework)
