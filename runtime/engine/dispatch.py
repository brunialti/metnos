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
from executor_helpers import catalog_names

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
    # §2.11 errore-runtime→form: osservazione needs_inputs (form get_inputs) che
    # il runtime a valle presenta all'utente invece dell'errore secco. Popolato
    # SOLO quando final_kind == "needs_inputs". Vedi _error_disambiguation_form.
    needs_inputs_obs: Optional[dict] = None


def _error_disambiguation_form(run, query: str) -> Optional[dict]:
    """§2.11 ERRORE-RUNTIME → FORM (25/6). Traduttore GENERALE: se l'ultimo step
    fallito porta nel result un segnale `disambiguation` strutturato, costruisce
    l'osservazione `needs_inputs` (form get_inputs) — l'utente sceglie invece di
    ricevere l'errore secco.

    Contratto (l'EXECUTOR emette, il recovery NON conosce i casi — §7.9):
      result["disambiguation"] = {
        "prompt": "<domanda>",                       # cosa chiedere
        "options": [{"value": v, "label": l}, ...],  # >=2 scelte
        "var": "<arg-name>",                         # opz, default "choice"
        "rerun": true|false,                         # opz, default true:
            # true  → on_complete ri-esegue la QUERY con l'arg scelto iniettato
            # (route_disambiguation pattern); l'arg-name finisce nel forced_*.
      }
    Ritorna None se: gate off, nessuno step, nessun segnale, <2 opzioni
    (niente da chiedere). Gate METNOS_ERROR_FORM — default OFF: il pilota è
    incompleto (il rerun-con-scelta `forced_args` è deferito a Fable, vedi
    project_error_to_form_pilot.md). Con default ON un path-not-found reale
    mostrerebbe un form che non si chiude. ON solo per test/quando Fable completa.
    """
    import os as _os
    if _os.environ.get("METNOS_ERROR_FORM", "0").lower() not in ("1", "true", "yes"):
        return None
    if not run or not getattr(run, "steps", None):
        return None
    last = run.steps[-1]
    r = last.result if isinstance(last.result, dict) else {}
    dis = r.get("disambiguation")
    if not isinstance(dis, dict):
        return None
    opts = dis.get("options")
    if not isinstance(opts, list) or len(opts) < 2:
        return None  # niente di decidibile → lascia il path errore normale
    # normalizza le opzioni a {value,label}
    norm = []
    for o in opts:
        if isinstance(o, dict) and "value" in o:
            norm.append({"value": o["value"], "label": o.get("label", str(o["value"]))})
        else:
            norm.append({"value": o, "label": str(o)})
    var = dis.get("var") or "choice"
    prompt = dis.get("prompt") or "Quale opzione intendi?"
    on_complete = ({"type": "rerun_query_disambiguated", "query": query,
                    "inject_arg": var}
                   if dis.get("rerun", True)
                   else {"type": "collect", "var": var})
    return {
        "decision": "needs_inputs",
        "needs_inputs": {
            "title": dis.get("title") or "Serve una tua scelta",
            "dialog": [{
                "var": var,
                "prompt": prompt,
                "schema": {"kind": "choice", "choices": norm},
                "optional": False,
            }],
            "fmt": "auto",
            "on_complete": on_complete,
            "timeout_s": 3600,
        },
    }


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
            vs = str(v)
            # I PLACEHOLDER non sono valori baked dalla query: `${stepN.field}`
            # (pipe da uno step a monte), `${RUNTIME:...}`, `${FILLER:...}` sono
            # risolti a RUNTIME col dato corrente — non discriminano la query.
            # Cache-key bug (24/6): create_events(start="${step1.entries.0.start}")
            # faceva fallire il grounding (token «step1»/«entries»/«0» non in
            # query) → ogni ripetizione re-pianificava (hit-rate ~1% sui compound
            # propose+fire). Skip i valori interamente-placeholder. §7.9.
            if "${" in vs:
                # Rimuovi i placeholder ${...} prima del check: ciò che resta
                # (eventuali literal misti) viene comunque verificato.
                vs = re.sub(r"\$\{[^}]*\}", " ", vs)
            for tok in re.findall(r"\d+|[a-z0-9._-]+/[a-z0-9._-]+", vs.lower()):
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
        names = catalog_names(catalog)
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
        names = catalog_names(catalog)
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


def _ensure_extract_clause(framework: Framework, intent, query: str,
                           catalog: Optional[list]) -> Framework:
    """§7.9 (bug live 21/6, banco compound-extract-create): la clausola «estrai»
    e' un TRANSFORM INTERMEDIO (produce record strutturati che il consumer
    create/write piping-consuma). Il proposer la droppa spesso e
    `_enforce_missing_clauses` non la recupera (append IN CODA → dopo il create,
    inutile; e derive(extract,messages) andava a None). Qui la INSERIAMO nella
    POSIZIONE giusta — subito dopo l'ultimo PRODUTTORE — con rewiring dei
    `from_step` (i consumer del produttore ora consumano l'extract; i ref a valle
    slittano +1). Scatta se l'intent ha {extract,*}.

    DUE casi (bug live 22/6 «missing 'fields'»): (1) extract_entries ASSENTE →
    INSERISCE con `fields` derivati dalla clausola «estrai X e Y»; (2) PRESENTE
    ma SENZA `fields` (il proposer lo emette spesso incompleto) → RIEMPIE `fields`
    deterministicamente. `fields` e' un arg REQUIRED. v3-gated, mai eccezioni."""
    try:
        from . import is_v3
        if not is_v3():
            return framework
        actions = getattr(intent, "actions", None) or []
        if not any(isinstance(a, dict) and (a.get("verb") or "") == "extract"
                   for a in actions):
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        if not steps:
            return framework
        import naming_grammar as _ng
        from compound_decomposer import (PRODUCER_VERBS as _PV,
                                         derive_extract_fields)
        from .types import StepSpec
        # extract_entries GIA' presente: il proposer a volte lo emette SENZA
        # l'arg required `fields` (bug live 22/6 → executor «missing 'fields'»).
        # Riempi `fields` DETERMINISTICAMENTE dalla clausola «estrai X e Y». Non
        # ne inseriamo un secondo. Se la query e' opaca → lascia com'e' (errore-
        # guida onesto a valle).
        existing = next((s for s in steps
                         if (getattr(s, "tool", "") or "") == "extract_entries"),
                        None)
        if existing is not None:
            ea = getattr(existing, "args", None) or {}
            if not ea.get("fields"):
                _ef = derive_extract_fields(query)
                if _ef:
                    ea["fields"] = _ef
                    existing.args = ea
                    log.info("[ensure_extract] fields riempiti su extract_entries "
                             "esistente: %s", _ef)
            return framework

        def _verb(s):
            nc = _ng.parse_name(getattr(s, "tool", "") or "")
            return nc.verb if nc else ""
        # L'extract va PRIMA del primo CONSUMER mutante (create/write/send/...):
        # cosi' produce i record che il consumer piping-usa. Ignora produttori
        # SPURI dopo il consumer.
        _CONSUMERS = {"create", "write", "send", "move", "delete", "share", "set"}
        ci = next((i for i, s in enumerate(steps) if _verb(s) in _CONSUMERS),
                  len(steps))
        pi = -1  # ultimo PRODUTTORE prima del consumer
        for i in range(ci):
            if _verb(steps[i]) in _PV:
                pi = i
        if pi < 0:  # nessun produttore prima del consumer → ultimo in assoluto
            for i, s in enumerate(steps):
                if _verb(s) in _PV:
                    pi = i
        if pi < 0:
            return framework  # nessun produttore → niente da estrarre
        prod_1b = pi + 1          # indice 1-based del produttore
        k = prod_1b + 1           # indice 1-based dove vivra' l'extract
        # Rewiring from_step PRIMA dell'insert: chi consumava il produttore ora
        # consuma l'extract; i ref a posizioni >= k slittano +1.
        for s in steps:
            fs = (getattr(s, "args", None) or {}).get("from_step")
            if isinstance(fs, int):
                if fs == prod_1b:
                    s.args["from_step"] = k
                elif fs >= k:
                    s.args["from_step"] = fs + 1
        # `fields` e' REQUIRED da extract_entries: derivalo DETERMINISTICAMENTE
        # dalla clausola «estrai X e Y» (bug live 22/6: senza, l'executor falliva
        # «missing 'fields'»). Se la query non espone i campi → niente fields:
        # l'executor dara' l'errore-guida onesto, ma il caso comune e' coperto.
        ins_args = {"from_step": prod_1b}
        _fields = derive_extract_fields(query)
        if _fields:
            ins_args["fields"] = _fields
        steps.insert(pi + 1, StepSpec(tool="extract_entries", args=ins_args))
        framework.steps = steps
        log.info("[ensure_extract] extract_entries inserito @1b=%d (dopo "
                 "produttore @%d) fields=%s", k, prod_1b, _fields or "—")
        return framework
    except Exception as ex:
        log.warning("ensure_extract_clause noop (best-effort): %r", ex)
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
        names = catalog_names(catalog)
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
        # 2a pass — oggetto ESTRANEO a TUTTO l'intent su step PRODUTTORE.
        # Bug live 21/6 (fatture Anthropic): clausola {find,messages} ma il
        # proposer-LLM compone `read_urls_html` (verbo `read` non fra i verbi
        # intent → il pass per-verbo sopra lo manca; object `urls` MAI chiesto
        # dall'intent). Generale: un produttore (read/find/get/list) con oggetto
        # assente da ogni oggetto-intent e' un misroute → riallinea al primo
        # oggetto-produttore dell'intent non gia' coperto. Intent-driven
        # (richiede dissenso esplicito), §7.9 deterministico, no LLM.
        try:
            from compound_decomposer import (PRODUCER_VERBS as _PRODV,
                                             derive_tool_name as _derive)
            from . import is_v3 as _is_v3_fn
            all_objs = {o for lst in by_verb.values() for o in lst}
            producer_objs = [a.get("object") for a in actions
                             if isinstance(a, dict)
                             and a.get("verb") in _PRODV and a.get("object")]
            if all_objs and producer_objs and _is_v3_fn():
                # v3 (banco caso 3): un produttore con oggetto preso SOLO da una
                # clausola CONSUMER (es. read_files per «salvali in un csv») e' un
                # fantasma del proposer flaky → riallinea o DROP. Vedi helper.
                if _align_foreign_producers_v3(framework, producer_objs,
                                               _PRODV, _derive, names, _ng):
                    changed = True
                    steps = getattr(framework, "steps", steps)
            elif all_objs and producer_objs:
                # v2/metis storico (byte-invariato): realign-only su all_objs.
                covered = set()
                for st in steps:
                    nc2 = _ng.parse_name(getattr(st, "tool", "") or "")
                    if nc2 and nc2.obj in all_objs:
                        covered.add(nc2.obj)
                for st in steps:
                    tool = getattr(st, "tool", None)
                    nc = _ng.parse_name(tool) if tool else None
                    if (not nc or nc.verb not in _PRODV
                            or nc.obj in all_objs):
                        continue  # non-produttore o oggetto gia' richiesto
                    target = next((o for o in producer_objs
                                   if o not in covered), producer_objs[0])
                    new_tool = _derive(nc.verb, target, names)
                    if new_tool and new_tool != tool:
                        st.tool = new_tool
                        # Gli args erano per il tool SBAGLIATO (es. read_urls_html
                        # con `urls=[...]`): inutili/dannosi per il nuovo tool con
                        # schema diverso. Azzera lasciando solo gli args runtime/
                        # pipe (`_actor/_lang/...`, `from_step/entries`) → il nuovo
                        # tool usa i suoi default (read_messages: max_results=500)
                        # e i passi a valle (extract/filter) selezionano.
                        old_args = getattr(st, "args", None) or {}
                        st.args = {k: v for k, v in old_args.items()
                                   if k.startswith("_")
                                   or k in ("from_step", "from", "entries")}
                        covered.add(target)
                        changed = True
        except Exception as ex:
            log.warning("align_objects foreign-obj noop: %r", ex)
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
    dropped = (qverbs & set(COVERAGE_REQUIRED_VERBS)) - fw_verbs
    # Famiglia PRODUTTORI interscambiabile (find/read/get/list): un produttore
    # qualunque nel framework copre ogni produttore richiesto (find_messages ==
    # read_messages, find_files copre «cerca i file», ...). Senza, «cerca...»
    # con read_X nel piano flaggava 'find' come droppato → enforce appendeva un
    # produttore SPURIO. §7.9 deterministico (bug live 21/6).
    _PRODUCERS = {"find", "read", "get", "list"}
    if (dropped & _PRODUCERS) and (fw_verbs & _PRODUCERS):
        dropped -= _PRODUCERS
    return dropped


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
        names = catalog_names(catalog)
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


def _decontaminate_clause_objects(intent, query: str) -> None:
    """§7.9 v3 (de-contaminazione, 19/6): corregge l'OGGETTO di una clausola di
    `intent.actions` quando l'LLM l'ha CONTAMINATO da una clausola vicina.

    Causa-radice provata: a 7-8 clausole, una clausola «trova le FOTO» dopo «trova
    i FILE» viene ancorata a `files` invece di `images` (bias attenzione, NON gap
    vocab: foto→images funziona isolato; NON risolto da tier wise). Il principio
    di non-contaminazione nel prompt regge a ≤6 clausole, cede a 8 con un'ancora
    forte (verbo identico + oggetto-schema-simile).

    Fix deterministico via la FUNZIONE che SOSTITUISCE i sinonimi (no liste
    cablate, no LLM): `prefilter._OBJECT_HINTS` deriva l'oggetto dal TESTO della
    clausola. Se dà un oggetto SPECIFICO e UNIVOCO che DIVERGE dall'oggetto
    assegnato dall'intent, corregge l'intent. CONSERVATIVO (anti-falso-positivo):
    corregge SOLO quando il testo-clausola contiene un hint NON-ambiguo (un solo
    oggetto candidato dai _OBJECT_HINTS) — mai su chunk ambigui/None. Muta
    `intent.actions` in place, PRIMA del pool. No-op su mono-azione.

    Universale: vale per ogni oggetto in _OBJECT_HINTS (foto/immagini→images,
    mail/posta→messages, ...), non cablato a un caso."""
    try:
        actions = [a for a in (getattr(intent, "actions", None) or [])
                   if isinstance(a, dict)]
        if len(actions) < 2:
            return
        from compound_decomposer import split_query_chunks
        import prefilter as _pf
        hints = getattr(_pf, "_OBJECT_HINTS", None)
        if not hints:
            return
        chunks = split_query_chunks(query)
        if len(chunks) != len(actions):
            return  # allineamento chunk↔action non garantito → astieniti (safe)

        def _obj_from_text(chunk: str):
            """Oggetto SPECIFICO e UNIVOCO dal testo (None se 0 o >1 candidati).
            Univoco = un solo object dei _OBJECT_HINTS ha un hint nel chunk."""
            cl = (chunk or "").lower()
            found = set()
            for obj, hs in hints.items():
                for h in hs:
                    # hint multi-parola: substring; singola: confine di parola.
                    if " " in h:
                        if h in cl:
                            found.add(obj); break
                    else:
                        import re as _re
                        if _re.search(r"\b" + _re.escape(h) + r"\b", cl):
                            found.add(obj); break
            return next(iter(found)) if len(found) == 1 else None

        changed = False
        for a, chunk in zip(actions, chunks):
            o = (a.get("object") or "").lower()
            text_obj = _obj_from_text(chunk)
            # corregge SOLO se il testo dà un oggetto univoco DIVERSO (contaminazione).
            # Mai se concordano o se ambiguo/None.
            if text_obj and o and text_obj != o:
                a["object"] = text_obj
                changed = True
        if changed:
            log.info("[decontaminate] oggetti-clausola corretti dal testo: %s",
                     [(a.get("verb"), a.get("object")) for a in actions])
    except Exception as ex:
        log.warning("decontaminate_clause_objects noop (best-effort): %r", ex)


def _fix_unroutable_verbs(intent, query: str, catalog: Optional[list]) -> None:
    """§7.9 v3 (verbo non-routabile, 19/6): corregge il VERBO di una clausola di
    intent.actions quando (verbo,oggetto) NON deriva alcun tool reale, MA il
    detector lessicale canonico sul testo della clausola dà un verbo che SÌ deriva
    un tool per quell'oggetto.

    Causa-radice (provata): a 7+ clausole l'LLM assegna un verbo valido-in-generale
    ma INESISTENTE per quell'oggetto (es. «apri i risultati» → (render,urls), ma
    NON c'è render_urls → la clausola si perde). Il testo «apri» → detector
    canonico = read → derive(read,urls)=get_urls (reale). Fix tool-existence-safe:
    flippa SOLO se il verbo attuale NON routa e il verbo-dal-testo SÌ. Usa le
    FUNZIONI canoniche (detect_canonical_verbs_all + derive_tool_name), zero
    sinonimi cablati. Muta intent.actions in place, PRIMA del pool. No-op mono."""
    try:
        actions = [a for a in (getattr(intent, "actions", None) or [])
                   if isinstance(a, dict)]
        if len(actions) < 2:
            return
        from compound_decomposer import split_query_chunks, derive_tool_name
        from prefilter import tokenize, detect_canonical_verbs_all
        names = catalog_names(catalog)
        names.discard(None)
        chunks = split_query_chunks(query)
        if len(chunks) != len(actions):
            return
        changed = False
        for a, chunk in zip(actions, chunks):
            v = (a.get("verb") or "").lower()
            o = (a.get("object") or "").lower()
            if not v or not o:
                continue
            # il verbo attuale routa? se sì, non toccare.
            if derive_tool_name(v, o, names):
                continue
            # quali verbi canonici dà il TESTO della clausola?
            cand = detect_canonical_verbs_all(tokenize(chunk)) or []
            for cv in cand:
                if cv != v and derive_tool_name(cv, o, names):
                    a["verb"] = cv
                    changed = True
                    break
        if changed:
            log.info("[fix_verbs] verbi non-routabili corretti dal testo: %s",
                     [(a.get("verb"), a.get("object")) for a in actions])
    except Exception as ex:
        log.warning("fix_unroutable_verbs noop (best-effort): %r", ex)


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


def _contains_stepref(obj, pos: int) -> bool:
    """True se `obj` (dict/list/str ricorsivo) contiene un ${stepN} con N==pos."""
    ref = "${step%d" % pos
    if isinstance(obj, str):
        return ref in obj
    if isinstance(obj, dict):
        return any(_contains_stepref(v, pos) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_stepref(v, pos) for v in obj)
    return False


def _step_is_consumed(steps, pos: int) -> bool:
    """True se un ALTRO step consuma l'output dello step in posizione `pos`
    (1-based): via `from_step`/`from_steps` o un riferimento ${stepN.field}.
    Serve a non DROPpare un produttore-fantasma che qualcuno consuma (romperebbe
    la pipe). §7.9 deterministico."""
    for i, s in enumerate(steps, 1):
        if i == pos:
            continue
        a = getattr(s, "args", None) or {}
        if not isinstance(a, dict):
            continue
        if a.get("from_step") == pos:
            return True
        fss = a.get("from_steps")
        if isinstance(fss, (list, tuple)) and pos in fss:
            return True
        if _contains_stepref(a, pos):
            return True
    return False


def _align_foreign_producers_v3(framework, producer_objs, _PRODV, _derive,
                                names, _ng) -> bool:
    """v3 (banco caso 3, 22/6): un PRODUTTORE (read/find/get/list) e' legittimo
    solo se il suo oggetto e' un oggetto-PRODUTTORE dell'intent. Un produttore con
    oggetto preso SOLO da una clausola CONSUMER — es. `read_files` per «salvali in
    un csv» (object=files dalla clausola write) — e' un FANTASMA del proposer
    flaky. Due esiti deterministici:
      - se l'oggetto-produttore reale NON e' ancora coperto da un produttore
        legittimo → RIALLINEA (è il solo produttore col verbo giusto e oggetto
        sbagliato; bug storico read_urls_html→read_messages);
      - se TUTTI gli oggetti-produttore sono gia' coperti E lo step e' ORFANO
        (nessuno lo consuma) → DROP onesto §2.8 + rimappa from_step a valle.
    §7.9 deterministico, no LLM. Ritorna True se ha modificato il framework.

    NB v2/metis: questa logica NON gira (gating is_v3 nel chiamante) → byte-
    invariato. Differenza vs v2: v2 usa `all_objs` (skip se l'oggetto compare
    in QUALSIASI clausola) e non DROPpa mai; v3 usa solo gli oggetti-produttore."""
    steps = list(getattr(framework, "steps", None) or [])
    producer_set = set(producer_objs)
    covered = set()
    for st in steps:
        nc2 = _ng.parse_name(getattr(st, "tool", "") or "")
        if nc2 and nc2.verb in _PRODV and nc2.obj in producer_set:
            covered.add(nc2.obj)
    changed = False
    to_drop = set()
    for st in steps:
        tool = getattr(st, "tool", None)
        nc = _ng.parse_name(tool) if tool else None
        if not nc or nc.verb not in _PRODV or nc.obj in producer_set:
            continue  # non-produttore o produttore con oggetto-intent legittimo
        if nc.obj == "entries":
            # `entries` e' il META-oggetto pipe in-memory (§2.2): find/read/
            # get_entries e' una lettura store/memoria LEGITTIMA che precede un
            # write/compare/filter su entries, anche quando l'intent decompone
            # solo la clausola-consumer {write,entries} (FASE 3). NON e' un
            # produttore-fantasma → mai drop/realign.
            continue
        uncovered = [o for o in producer_objs if o not in covered]
        if uncovered:
            new_tool = _derive(nc.verb, uncovered[0], names)
            if new_tool and new_tool != tool:
                st.tool = new_tool
                old_args = getattr(st, "args", None) or {}
                st.args = {k: v for k, v in old_args.items()
                           if k.startswith("_")
                           or k in ("from_step", "from", "entries")}
                covered.add(uncovered[0])
                changed = True
        else:
            pos = steps.index(st) + 1
            if not _step_is_consumed(steps, pos):
                to_drop.add(id(st))
                changed = True
    if to_drop:
        kept = [s for s in steps if id(s) not in to_drop]
        old_pos = {id(s): i + 1 for i, s in enumerate(steps)}
        new_pos = {id(s): i + 1 for i, s in enumerate(kept)}
        idx_map = {old_pos[id(s)]: new_pos[id(s)] for s in kept}
        for s in kept:
            s.args = _remap_step_refs(getattr(s, "args", None) or {}, idx_map)
        framework.final_message = _remap_step_refs(
            getattr(framework, "final_message", "") or "", idx_map)
        framework.steps = kept
    return changed


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
        # Rank = POSIZIONE in intent.actions (ordine autoritativo). Robusto ai
        # DUPLICATI (bug a8d3, fwd=1): il vecchio Pass-1 «ogni step pesca la prima
        # action libera» esauriva le action coi duplicati same-object (compute×2,
        # write×2) → gli step extra floatavano con rank sbagliati → sort fuori
        # posto. Nuovo design a 2 passate:
        #  Pass-A: ogni step HARD prende il rank dell'action col MEDESIMO oggetto
        #          NON ancora consumata, in ordine intent (consumo stabile). Un
        #          duplicato (oggetto gia' esaurito) NON consuma: resta senza rank.
        #  Pass-B: gli step senza rank (duplicati, SOFT, extra) EREDITANO il rank
        #          dello step HARD PRECEDENTE + epsilon crescente → restano
        #          ADIACENTI al loro gruppo, non floatano. SOFT idem.
        # match: PRODUCER step ↔ action stesso-oggetto (producer o transform);
        #        MUTATOR step ↔ action stesso-verbo. (invariato, ma per-oggetto.)
        remaining = list(enumerate(actions))
        hard_rank: dict = {}
        for st in execs:
            nc = _ng.parse_name(st.tool or "")
            sv = (nc.verb if nc else "") or ""
            so = (nc.obj if nc else "") or ""
            if sv in _SOFT:
                continue
            best_j = None
            for j, (ai, a) in enumerate(remaining):
                av = (a.get("verb") or "").lower()
                ao = (a.get("object") or "").lower()
                if sv in _PROD:
                    ok = bool(so and so == ao and (av in _PROD or av in _SOFT))
                else:
                    ok = bool(sv and sv == av)
                if ok:
                    best_j = j
                    break
            if best_j is None:
                continue  # duplicato/non-mappabile → erediterà in Pass-B
            ai, _a = remaining.pop(best_j)
            hard_rank[id(st)] = ai
        # Pass-B: rank finale. Gli step senza match ereditano il rank dell'ULTIMO
        # step HARD rankato (adiacenza) con epsilon crescente per stabilita'; gli
        # step in testa prima di ogni HARD ereditano il rank del PRIMO HARD - 1
        # (restano davanti al loro gruppo, ma il sort poi li mette in ordine).
        ranks: dict = {}
        last = None
        eps = 0.0
        for st in execs:
            if id(st) in hard_rank:
                last = float(hard_rank[id(st)])
                eps = 0.0
                ranks[id(st)] = last
            else:
                eps += 0.001
                ranks[id(st)] = (last if last is not None else -1.0) + eps
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
        names = catalog_names(catalog)
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


# ── Cap di conteggio allucinato dal proposer (§2.1: "cap superiore = parametro
# ESPLICITO") ────────────────────────────────────────────────────────────────
# L'LLM proposer tende a iniettare un cap piccolo ("top 10") anche quando l'utente
# NON ha indicato una quantità: «riassumi i file .md» vuole TUTTI i file, non i
# primi 10. Senza una quantità nella clausola quel cap è rumore → va tolto, così
# l'executor applica il suo default (deliberato, di norma più generoso). Generale
# su ogni executor con cap, deterministico (§7.9). Bug live turn 4648c5c3.
_COUNT_CAP_ARGS = frozenset({"top_k", "max_results", "max_total", "top", "limit"})

# Lessico CHIUSO IT+EN — numeri-parola e nomi-conteggio/tempo. Elenco finito (come
# vocab.QUALIFIERS / args_extractor._LANG_EXT_MAP), NON un dizionario di sinonimi.
_NUMWORD = (r"uno|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci|undici|"
            r"dodici|venti|trenta|quaranta|cinquanta|cento|mille|"
            r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
            r"twenty|thirty|forty|fifty|hundred|thousand")
# Cosa si conta: un numero che li precede è una quantità-RISULTATO.
_COUNT_NOUN = (r"file|files|mail|email|messaggi?|foto|immagini?|righe|line[ae]|"
               r"lines?|risultati?|results?|elementi?|element|items?|entr(?:y|ies)|"
               r"record|records?|documenti?|docs?|pdf|url|urls|link|links|"
               r"pagin[ae]|pages?|foglio|fogli|sheet|sheets")
# Nomi-TEMPO: un numero che li precede è una finestra temporale, NON un cap di
# conteggio (de-conflazione, cfr. «12 mesi»→time_window, non max_results=12).
_TIME_NOUN = (r"giorni?|or[ae]|settiman[ae]|mes[ei]|ann[oi]|minut[oi]|second[oi]|"
              r"days?|hours?|weeks?|months?|years?|minutes?|seconds?")
# Selettore di testa che, seguito da un numero (e NON da un nome-tempo), esprime
# un limite di risultati: «primi 10», «solo 5», «top 3», «first 5», «at most 20».
_SELECTOR = (r"prim[ie]|ultim[ie]|sol[ie]|soltanto|appena|massimo|almeno|top|"
             r"first|last|only|just|at\s+most|up\s+to")
_QTY_FRAME_RE = re.compile(
    r"(?:\b(?:" + _SELECTOR + r")\s+(?:\d+|" + _NUMWORD + r")\b"
    r"(?!\s*(?:" + _TIME_NOUN + r")\b))"
    r"|(?:\b(?:\d+|" + _NUMWORD + r")\s+(?:" + _COUNT_NOUN + r")\b)",
    re.IGNORECASE)


def _clause_requests_count(text: str) -> bool:
    """True se la clausola esprime una QUANTITÀ di risultati (numero in un frame
    di conteggio: «primi 10», «10 file», «top 5», numeri-parola inclusi). Esclude
    le finestre temporali («ultimi 7 giorni»). Deterministico, no LLM."""
    return bool(text) and _QTY_FRAME_RE.search(text) is not None


def _demote_overtight_caps(args: dict, schema: dict, clause: str) -> None:
    """Toglie da `args` un cap di conteggio messo dal proposer quando (a) la
    clausola NON chiede una quantità e (b) il default dell'executor è PIÙ
    inclusivo (intero maggiore, o 0=illimitato). Così senza una quantità esplicita
    dell'utente il planner non può rendere il risultato meno inclusivo del default
    deliberato dell'executor (§2.1/§2.7). Muta `args` sul posto. No-op se la
    clausola chiede una quantità o il default non è un intero dichiarato."""
    if not isinstance(args, dict) or not isinstance(schema, dict):
        return
    if _clause_requests_count(clause or ""):
        return
    props = schema.get("properties") if isinstance(
        schema.get("properties"), dict) else schema
    for name in _COUNT_CAP_ARGS:
        if name not in args:
            continue
        val = args.get(name)
        # 0/negativo = già illimitato/assente; bool non è un cap numerico.
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            continue
        default = (props.get(name) or {}).get("default") \
            if isinstance(props.get(name), dict) else None
        more_inclusive = default == 0 or (
            isinstance(default, int) and not isinstance(default, bool)
            and default > val)
        if more_inclusive:
            del args[name]


def _fill_clause_args(framework: Framework, intent, query: str,
                      catalog: Optional[list]) -> Framework:
    """§7.9 v3 (fase ARGS): riempie gli args DEDUCIBILI di ogni step dal testo
    della SUA clausola (non dalla query intera). Una query multi-clausola ha piu'
    date/path/destinatari: l'estrazione full-query lega il valore SBAGLIATO allo
    step (es. time_window di «ieri» applicato allo step di «oggi»). Qui ogni step
    viene mappato alla sua clausola (split deterministico, ordine) e
    `args_extractor.regex_extract` estrae SOLO da quel chunk, riempiendo i campi
    DICHIARATI dallo schema e ANCORA assenti (mai sovrascrive cio' che il
    proposer ha gia' messo). Deterministico, no LLM. No-op su mono-azione.

    Lo store-name di una clausola entries si deriva dal chunk (sost. dopo
    «spese/archivio/store …»): generale, non un mapping cablato."""
    try:
        from compound_decomposer import (split_query_chunks,
                                         detect_chunk_action)
        import args_extractor as _ax
        import naming_grammar as _ng
        steps = [s for s in (getattr(framework, "steps", None) or [])
                 if (s.tool or "") != "final_answer"]
        if not steps:
            return framework
        cat_by_name0 = {getattr(e, "name", None): e for e in (catalog or [])}
        # Mono-step: nessuna ambiguità di clausola → estrai dalla query INTERA
        # i campi deducibili e ANCORA assenti (il proposer LLM spesso omette il
        # `pattern` o mette '*'). Es. «quanti file python» → pattern=*.py +
        # count_only. Deterministico, riempie solo i buchi (§7.3, generale).
        if len(steps) == 1:
            st = steps[0]
            e = cat_by_name0.get(st.tool)
            schema = getattr(e, "args_schema", None) if e else None
            if isinstance(schema, dict):
                try:
                    extracted = _ax.regex_extract(query, schema)
                except Exception:
                    extracted = {}
                if extracted:
                    cur = dict(st.args or {})
                    for k, v in extracted.items():
                        # riempi i buchi E corregge il glob-universale del
                        # proposer: un `pattern='*'` non porta informazione,
                        # l'estrattore conosce il tipo-file richiesto.
                        if (k not in cur or cur[k] in (None, "", [], {})
                                or (k in ("pattern", "patterns", "glob")
                                    and cur.get(k) in ("*", "*.*"))):
                            cur[k] = v
                    st.args = cur
                # Cap allucinato: la query mono-clausola è la clausola intera.
                if isinstance(st.args, dict):
                    _demote_overtight_caps(st.args, schema, query)
            return framework
        chunks = split_query_chunks(query)
        if len(chunks) < 2:
            # Una sola clausola ma PIÙ step (es. find→read→describe da una
            # richiesta unica «riassumi i file .md»): nessun chunk da mappare,
            # ma il cap allucinato del produttore va tolto comunque — la
            # clausola è la query intera. Senza questo, il caso del bug
            # (mono-clausola, multi-step) sfuggiva a entrambi i rami.
            for st in steps:
                e = cat_by_name0.get(st.tool)
                schema = getattr(e, "args_schema", None) if e else None
                if isinstance(schema, dict) and isinstance(st.args, dict):
                    _demote_overtight_caps(st.args, schema, query)
            return framework
        cat_by_name = {getattr(e, "name", None): e for e in (catalog or [])}
        # Allinea step↔chunk per OGGETTO (il reorder + gli helper SOFT + le
        # clausole multi-chunk scompaginano l'ordine → lo zip posizionale legava
        # il chunk SBAGLIATO). L'oggetto del chunk si deduce, in ordine di
        # priorita': (1) intent.actions — la decomposizione LLM e' ORDINATA e
        # allineata ai chunk per costruzione, e mappa i termini naturali
        # (foto→images, impegni→events) che il detector lessicale non copre;
        # (2) detect_chunk_action lessicale come fallback. Cosi' ogni step trova
        # il chunk col suo oggetto, consumato una volta in ordine.
        actions = [a for a in (getattr(intent, "actions", None) or [])
                   if isinstance(a, dict)]
        chunk_obj = []
        for i, ch in enumerate(chunks):
            o = None
            if i < len(actions):
                o = (actions[i].get("object") or "").lower() or None
            if not o:
                try:
                    act = detect_chunk_action(ch)
                    o = act[1] if act else None
                except Exception:
                    o = None
            chunk_obj.append((ch, o))
        used = [False] * len(chunks)
        # PASS 1 (prioritario): ogni step→chunk per OGGETTO ESATTO. Cosi' il
        # match semantico non viene rubato dal fallback posizionale di uno step
        # precedente (bug: un helper o un duplicato consumava il chunk «foto»
        # prima che find_images lo reclamasse). PASS 2: fallback posizionale solo
        # sugli step ancora senza chunk, sui chunk ancora liberi.
        step_chunk: dict = {}
        for pos, st in enumerate(steps):
            nc = _ng.parse_name(st.tool or "")
            so = (nc.obj if nc else None)
            if not so:
                continue
            for i, (ch, co) in enumerate(chunk_obj):
                if not used[i] and co and co == so:
                    used[i] = True
                    step_chunk[pos] = ch
                    break
        for pos, st in enumerate(steps):
            if pos in step_chunk:
                continue
            if pos < len(chunks) and not used[pos]:
                used[pos] = True
                step_chunk[pos] = chunks[pos]

        for pos, st in enumerate(steps):
            e = cat_by_name.get(st.tool)
            schema = getattr(e, "args_schema", None) if e else None
            if not isinstance(schema, dict):
                continue
            chunk = step_chunk.get(pos)
            if not chunk:
                continue
            # Cap allucinato per-clausola: usa il chunk dello step (non la query
            # intera: un numero di un'ALTRA clausola non deve salvare questo cap).
            if isinstance(st.args, dict):
                _demote_overtight_caps(st.args, schema, chunk)
            try:
                extracted = _ax.regex_extract(chunk, schema)
            except Exception:
                extracted = {}
            props = (schema.get("properties") or {})
            # store-name per clausole entries (dichiara 'store') dal chunk.
            if "store" in props and "store" not in (st.args or {}):
                m = re.search(r"\b(?:nello? store|nell'archivio|fra le|tra le|"
                              r"dallo? store|nelle?)\s+([a-zàèéìòù0-9_]+)",
                              chunk.lower())
                if m and m.group(1) not in ("store", "archivio"):
                    extracted.setdefault("store", m.group(1))
            if not extracted:
                continue
            cur = dict(st.args or {})
            for k, v in extracted.items():
                if k not in cur or cur[k] in (None, "", [], {}):
                    cur[k] = v
            st.args = cur
        return framework
    except Exception as ex:
        log.warning("fill_clause_args noop (best-effort): %r", ex)
        return framework


# ── Store field-ref resolver (v3, 20/6/2026) ───────────────────────────────
# Tool che LEGGONO da uno store generico NOMINATO: producono entries con le
# COLONNE dello store (find/read/get_entries).
_STORE_READ_TOOLS = frozenset({"find_entries", "read_entries", "get_entries"})
# Tool che passano le entries oltre senza cambiarne lo SCHEMA-colonne: il campo
# di provenienza store resta valido a valle (filter/sort/group/...).
_STORE_PASSTHROUGH_PREFIXES = ("filter_", "sort_", "group_", "classify_",
                               "compare_", "compute_")
# Colonna «testo della risposta» per un body/commento per-entry lasciato
# LETTERALE dal proposer (semantica: il corpo di un commento per-issue È la
# risposta accettata). Priorità: la risposta ACCETTATA prima della bozza.
_REPLY_COL_PRIORITY = ("accepted_reply", "reply", "answer", "body", "message",
                       "comment", "content", "text", "draft_reply")
_BODY_TEMPLATE_KEYS = frozenset({"body_template", "message_template",
                                 "comment_template", "text_template"})
_FIELD_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _registered_store_schema(store_name):
    """(columns dict, primary_key tuple) per uno store REGISTRATO, o (None,None).
    No-op se non registrato (nome sbagliato/assente): il resolver non tocca la
    pipeline. Universale: legge il registry, nessun nome cablato."""
    try:
        import store as _store
        if not store_name or not _store.is_registered(store_name):
            return None, None
        sch = _store.get_store(store_name).schema
        return dict(sch.columns), tuple(sch.primary_key or ())
    except Exception:  # noqa: BLE001 — best-effort
        return None, None


def _trace_source_store(steps_1based: dict, start_1based) -> Optional[str]:
    """Risale la catena from_step da `start` fino a un produttore store-read;
    ritorna il nome dello store (o None). Attraversa i pass-through che NON
    cambiano lo schema-colonne (filter/sort/group/...)."""
    cur = start_1based
    seen: set = set()
    while isinstance(cur, int) and cur not in seen and cur in steps_1based:
        seen.add(cur)
        st = steps_1based[cur]
        tool = st.tool or ""
        if tool in _STORE_READ_TOOLS:
            return (st.args or {}).get("store")
        if tool.startswith(_STORE_PASSTHROUGH_PREFIXES):
            cur = (st.args or {}).get("from_step")
            continue
        break
    return None


def _alias_field_to_col(field: str, cols_lower: dict) -> Optional[str]:
    """Mappa un riferimento di campo alla colonna REALE, o None. Match: esatto
    (case-insensitive); colonna UNICA che termina in `_<field>` (number→
    issue_number). Niente match ambiguo/assente (no falsi accoppiamenti)."""
    f = (field or "").lower()
    if not f:
        return None
    if f in cols_lower:
        return cols_lower[f]
    suf = [orig for low, orig in cols_lower.items() if low.endswith("_" + f)]
    return suf[0] if len(suf) == 1 else None


def _reply_column(cols_lower: dict) -> Optional[str]:
    for name in _REPLY_COL_PRIORITY:
        if name in cols_lower:
            return cols_lower[name]
    for low, orig in cols_lower.items():
        if "reply" in low or "answer" in low:
            return orig
    return None


def _resolve_store_field_refs(framework: Framework) -> Framework:
    """§7.9 v3 (fase ARGS, 20/6): ricuce i riferimenti-campo degli step a VALLE
    di un produttore store-read contro lo SCHEMA REALE dello store (registry).
    Il proposer NON vede lo schema dello store → indovina nomi-campo ({number}
    vs colonna issue_number), lascia corpi LETTERALI (vs {accepted_reply}) e mette
    ${FILLER:repo} su colonne che le entries GIÀ portano. Deterministico, no LLM,
    no-op se nessun produttore store-read REGISTRATO a monte. Ripara:
      - `<arg>_template`: rimappa i placeholder {P} a colonne reali via alias
        UNIVOCO; un body/commento per-entry LETTERALE → {colonna-risposta}.
      - scalari il cui NOME è una colonna e il VALORE è ${FILLER:..} (il proposer
        non sapeva il valore) → rimossi: l'executor vettoriale li riempie
        per-entry dal campo omonimo dell'entry (es. repo=brunialti/metnos).
      - `key` di write_entries verso uno store registrato → primary_key dello
        store (la chiave d'upsert È la PK; una key inventata su colonna NON
        indicizzata romperebbe ON CONFLICT).
    Universale: vale per OGNI store registrato, nessun nome cablato."""
    try:
        all_steps = list(getattr(framework, "steps", None) or [])
        steps_1based = {i + 1: s for i, s in enumerate(all_steps)}
        for st in all_steps:
            tool = st.tool or ""
            if tool == "final_answer":
                continue
            args = dict(st.args or {})
            changed = False
            # (W) write_entries verso store registrato: key = primary_key.
            if tool == "write_entries":
                cols_w, pk_w = _registered_store_schema(args.get("store"))
                if cols_w and pk_w and args.get("key") != list(pk_w):
                    args["key"] = list(pk_w)
                    changed = True
            # Riferimenti-campo verso le ENTRIES consumate (from_step → store).
            fs = args.get("from_step")
            cols = None
            if isinstance(fs, int):
                cols, _pk = _registered_store_schema(
                    _trace_source_store(steps_1based, fs))
            if cols:
                cols_lower = {c.lower(): c for c in cols}
                for k in list(args.keys()):
                    v = args[k]
                    # (T) *_template: alias dei placeholder + corpo per-entry.
                    if isinstance(k, str) and k.endswith("_template") \
                            and isinstance(v, str):
                        nv = v
                        for p in _FIELD_PLACEHOLDER_RE.findall(v):
                            if p.lower() in cols_lower:
                                continue
                            col = _alias_field_to_col(p, cols_lower)
                            if col:
                                nv = re.sub(r"\{" + re.escape(p) + r"\}",
                                            "{" + col + "}", nv)
                        # body/commento per-entry che NON pesca da ALCUNA colonna
                        # reale (corpo letterale, o placeholder fantasma come
                        # {body}) → colonna-risposta: un corpo fisso/segnaposto
                        # verrebbe postato così com'è (LLM incostante sul body).
                        if k in _BODY_TEMPLATE_KEYS and not any(
                                p.lower() in cols_lower
                                for p in _FIELD_PLACEHOLDER_RE.findall(nv)):
                            rc = _reply_column(cols_lower)
                            if rc:
                                nv = "{" + rc + "}"
                        if nv != v:
                            args[k] = nv
                            changed = True
                        continue
                    # (S) scalare = colonna con ${FILLER:..} non risolto → drop:
                    # l'executor lo riempie per-entry dal campo omonimo.
                    if isinstance(k, str) and k.lower() in cols_lower \
                            and isinstance(v, str) and "${FILLER:" in v:
                        del args[k]
                        changed = True
            if changed:
                st.args = args
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort, non blocca il turno
        log.warning("resolve_store_field_refs noop (best-effort): %r", ex)
        return framework


def _route_mail_delete_to_trash(framework: Framework,
                                catalog: Optional[list]) -> Framework:
    """§5 deterministico (bug live 7c4390f1, 23/6): in Metnos NON esiste
    `delete_messages` — cancellare mail = `move_messages(dst_folder="Trash")`.
    Il proposer, vedendo {delete,messages} e nessun `delete_messages`, ripiega
    su `delete_entries(store="messages")` (store inesistente → fallisce: «store
    messages non registrato»). Qui riscriviamo quel passo a
    `move_messages(dst_folder="Trash")`, propagando `from_step` e `account` dal
    produttore mail. Tool-existence-safe: solo se `move_messages` nel catalogo.
    §7.9 deterministico, no LLM. Idempotente: un `move_messages` non rimatcha.
    Gira DOPO `_enforce_missing_clauses` (che ha gia' visto il delete_entries
    come clausola-delete soddisfatta → non la ri-aggiunge)."""
    try:
        names = catalog_names(catalog)
        if "move_messages" not in names:
            return framework

        def _is_mail_producer(t):
            return (isinstance(t, str) and t.endswith("_messages")
                    and t not in ("move_messages", "send_messages", "set_messages"))

        steps = framework.steps
        for i, s in enumerate(steps):
            if (s.tool or "") != "delete_entries":
                continue
            store = str(s.args.get("store") or "").lower()
            fs = s.args.get("from_step")
            prod_idx = (fs - 1) if (isinstance(fs, int) and 1 <= fs <= len(steps)) else None
            consumes_mail = prod_idx is not None and _is_mail_producer(steps[prod_idx].tool)
            if not (store == "messages" or consumes_mail):
                continue
            if prod_idx is None:  # wira al produttore mail precedente
                for j in range(i - 1, -1, -1):
                    if _is_mail_producer(steps[j].tool):
                        prod_idx = j
                        break
            new_args = {"dst_folder": "Trash"}
            if prod_idx is not None:
                new_args["from_step"] = prod_idx + 1
                acct = steps[prod_idx].args.get("account")
                if acct:
                    new_args["account"] = acct
            s.tool = "move_messages"
            s.args = new_args
            s.if_prev_entries_nonempty = True  # mutating: mai su 0 elementi
            log.info("[mail_delete §5] delete_entries(messages) -> "
                     "move_messages(dst_folder=Trash)")
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort, non blocca il turno
        log.warning("route_mail_delete_to_trash noop (best-effort): %r", ex)
        return framework


# Marcatori di pluralità: la query chiede PIÙ file con quel nome, non un path
# univoco. «i/tutti i/ogni/gli file X» → X è un PATTERN da cercare, non UN path.
_PLURAL_FILE_MARKERS = (
    "tutti i", "tutti gli", "i file", "gli file", "ogni file", "i files",
    "all the", "all ", "every ", "the files", "each ",
)


def _route_filename_pattern_to_find(framework: Framework, query: str,
                                    catalog: Optional[list]) -> Framework:
    """§4.3 deterministico — «no path INVENTATO: FIND prima di READ». Un
    `read_<obj>[_provider]` con `paths=[X]` dove X è un NOME-FILE (basename) che
    l'utente ha citato come PLURALE («i/tutti i file readme.md») non è UN path
    noto: è un PATTERN da cercare. Il proposer copia X dal PATTERN del manifest
    (es. read_files_github PATTERN mostra `paths=["README.md"]`, §2.5 magnetico)
    → legge UN solo file. Qui si INSERISCE prima il FIND gemello
    (`find_<obj>[_provider](pattern=X)`) e si ricuce il read alle sue entries
    (`from_step`) → catena find→read→describe su TUTTI i file.

    GENERALE (non per-github): vale per ogni coppia read/find dello stesso object
    e provider (read_files↔find_files, read_files_github↔find_files_github). Il
    find gemello si deriva dal nome (read→find, stesso suffisso). Tool-existence-
    safe (solo se il find gemello è nel catalog) + idempotente (read già
    `from_step` non rimatcha). No LLM. Bug live 22f32adb/582b4824 (26/6)."""
    try:
        names = catalog_names(catalog)
        q = (query or "").lower()
        if not any(m in q for m in _PLURAL_FILE_MARKERS):
            return framework
        steps = framework.steps
        for i, s in enumerate(steps):
            tool = s.tool or ""
            # read_<obj>... con object files/dirs (i soli con un FIND-per-pattern).
            if not tool.startswith("read_"):
                continue
            rest = tool[len("read_"):]
            if not (rest.startswith("files") or rest.startswith("dirs")):
                continue
            find_twin = "find_" + rest          # stesso object + provider
            if find_twin not in names:
                continue
            if isinstance(s.args.get("from_step"), int):  # già instradato
                continue
            paths = s.args.get("paths")
            if isinstance(paths, str):
                paths = [paths]
            if not (isinstance(paths, list) and len(paths) == 1):
                continue
            name = str(paths[0]).strip()
            # NOME-FILE basename: no '/', no glob universale, e citato in query.
            if (not name or "/" in name or name in ("*", "*.*")
                    or name.lower() not in q):
                continue
            # find gemello: pattern=X, propaga repo/base_path dal read.
            find_args = {"pattern": name}
            for k in ("repo", "base_path", "path_prefix", "ref"):
                if k in s.args:
                    find_args[k] = s.args[k]
            s.args = {k: v for k, v in s.args.items() if k != "paths"}
            s.args["from_step"] = i + 1          # find inserito a i → step i+1
            steps.insert(i, StepSpec(tool=find_twin, args=find_args))
            framework.steps = steps
            log.info("[filename_pattern §4.3] %s(paths=[%r]) -> %s(pattern=%r)"
                     " + read(from_step)", tool, name, find_twin, name)
            break  # un solo aggancio per turno
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort, non blocca il turno
        log.warning("route_filename_pattern_to_find noop (best-effort): %r", ex)
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
    produttori-object droppati (multi-dominio), `_conform_to_intent_order`
    riordina su intent.actions, infine `_fill_clause_args` riempie gli args
    deducibili per-clausola (gated is_v3() → v2 byte-invariato)."""
    framework = _align_framework_objects(framework, intent, catalog)
    framework = _enforce_missing_clauses(framework, intent, query, catalog)
    from . import is_v3
    if is_v3():
        framework = _enforce_missing_objects(framework, intent, query, catalog)
        framework = _ensure_extract_clause(framework, intent, query, catalog)
        framework = _conform_to_intent_order(framework, intent, query, catalog)
        framework = _fill_clause_args(framework, intent, query, catalog)
        # Ricuce i riferimenti-campo (template/key) degli step a valle di un
        # produttore store-read allo SCHEMA REALE dello store (20/6): chiude il
        # misfit {number}/body-letterale/${FILLER:repo} che il proposer fa non
        # vedendo lo schema. Dopo fill: opera sugli args ormai stabili.
        framework = _resolve_store_field_refs(framework)
    # §5: cancellare mail = move_messages(Trash) — riscrive il fallback
    # delete_entries(store=messages) del proposer. Dopo enforce (gia' visto
    # il delete come clausola soddisfatta) e fuori dal gate v3 (vale sempre).
    framework = _route_mail_delete_to_trash(framework, catalog)
    # §4.3: «i/tutti i file <nome>» → find_<obj>(pattern)+read, non un read di UN
    # path INVENTATO (no path inventato: FIND prima). Generale read/find di ogni
    # object+provider. Fuori dal gate v3 (vale sempre); dopo i guard di struttura.
    framework = _route_filename_pattern_to_find(framework, query, catalog)
    return framework


def _insert_consent_gate_if_scheduled(framework, query: str, runtime_ctx):
    """§7.9 consent-gate (20/6/2026): in un turno SCHEDULATO una pipeline che
    comunica verso l'ESTERNO (`send_*`) NON parte senza consenso umano →
    inserisce un `get_approval` PRIMA della prima azione `send_*`, con riassunto
    (conteggio) dal produttore a monte. La pausa+ripresa la gestisce FIX 1
    (gate-resume): on-approve la pipeline si riesegue PULITA col gate auto-passato.

    Universale §7.9: il gate e' inserito dal RUNTIME (deterministico), non
    composto dal proposer — cosi' la pipeline compone pulita (variant A) e
    l'ordine/from_step restano corretti. Skip se: ripresa post-approvazione
    (`_gate_approved`), turno NON schedulato, o gate gia' presente. No-op se
    nessun send_*."""
    try:
        if (runtime_ctx or {}).get("_gate_approved"):
            return framework
        from treated_issues_guard import is_scheduled_turn
        if not is_scheduled_turn():
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        if any((s.tool or "") == "get_approval" for s in steps):
            return framework
        first_send = next((idx for idx, s in enumerate(steps)
                           if (s.tool or "").startswith("send_")), None)
        if first_send is None:
            return framework
        # Conteggio: lo step che il SEND consuma (from_step) = gli item che
        # partiranno DAVVERO (post-filtro), non il produttore grezzo a monte.
        # Fallback: il produttore find/read/get/list piu' vicino. ${stepN.@count}
        # risolto a runtime dall'engine (ora anche negli args, vedi executor).
        count_1based = None
        fs = steps[first_send].args.get("from_step")
        if isinstance(fs, int) and 1 <= fs <= first_send:
            count_1based = fs
        else:
            for j in range(first_send - 1, -1, -1):
                if (steps[j].tool or "").split("_", 1)[0] in (
                        "find", "read", "get", "list"):
                    count_1based = j + 1
                    break
        from messages import get as _msg_get
        if count_1based:
            # Prompt con riassunto BREVE per-item (Roberto 20/6: dire COSA si
            # approva, non solo quanti) — `@brief` rende «#53 titolo…; #54 …»
            # dal produttore a monte del send. `@count` resta per il numero.
            # I due magic si risolvono a runtime (executor); il brief e' capato
            # (3 item) per stare nel limite prompt di get_approval.
            prompt = _msg_get("MSG_CONSENT_GATE_OUTBOUND_BRIEF",
                              n=f"${{step{count_1based}.@count}}",
                              brief=f"${{step{count_1based}.@brief}}")
        else:
            prompt = _msg_get("MSG_CONSENT_GATE_OUTBOUND")
        from .types import StepSpec
        # channel+actor dal runtime_ctx → get_approval rende il FORM A PULSANTI
        # nativo del canale (Telegram inline: Approva/Disapprova/Annulla) invece
        # del fallback testuale, e salva il dialog pending sotto il sender
        # CORRETTO («telegram:roberto») così il tap/ripresa lo ritrova. Roberto
        # 20/6: il gate DEVE usare i pulsanti, non una risposta digitata.
        rc = runtime_ctx or {}
        # timeout generoso: un'approvazione SCHEDULATA outbound si tappa con
        # comodo (Telegram, anche minuti/ore dopo) → 1h (= cap get_approval),
        # non il default. Roberto 20/6: niente quick-close in conversazione.
        gate_args = {
            "prompt": prompt,
            "on_approve": {"tool": "final_answer", "args": {}},
            "timeout_s": 3600,
            "channel": rc.get("channel") or "",
            "actor": rc.get("actor") or "",
        }
        if count_1based:
            # §2.11/§2.8: outbound VUOTO = niente da approvare. `guard_count` si
            # risolve a runtime (${stepN.@count}); se il produttore ha 0 item,
            # get_approval passa trasparente SENZA dialog (niente «approvo 0
            # elementi?», bug live 22/6). Il send a valle resta no-op onesto.
            gate_args["guard_count"] = f"${{step{count_1based}.@count}}"
        gate = StepSpec(tool="get_approval", args=gate_args)
        framework.steps = steps[:first_send] + [gate] + steps[first_send:]
        log.info("[consent_gate] get_approval inserito prima di send "
                 "(turno schedulato, outbound)")
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("[consent_gate] noop: %r", ex)
        return framework


def _inject_gate_resume_if_paused(run, query: str, runtime_ctx) -> None:
    """gate-resume (20/6/2026): se un gate get_approval ha messo in PAUSA la
    pipeline (run.gate_dialog_id), sovrascrive l'on_complete del dialog
    (gate_dispatch → resume_engine_gate) col contesto di ripresa. On-approve il
    callback riesegue il turno con `pre_approved_gate` → il gate auto-passa e
    gli step a valle (send/write) girano. §7.9 deterministico. No-op se nessun
    gate in pausa. Universale: vale per ogni compound con un gate di consenso."""
    did = getattr(run, "gate_dialog_id", "") if run else ""
    if not did:
        return
    rc = runtime_ctx or {}
    actor = rc.get("actor") or "host"
    channel = rc.get("channel") or ""
    sender = f"{channel}:{actor}" if channel else actor
    try:
        import dialog_pending as _dp
        st = _dp.load_pending(sender, did)
        if not st:
            return
        oc = st.get("on_complete") or {}
        st["on_complete"] = {
            "type": "resume_engine_gate",
            "original_query": query,
            "conversation_id": rc.get("conversation_id") or "",
            "gate_approve_value": oc.get("approve_value", "approve"),
            "gate_on_reject": oc.get("on_reject"),
        }
        _dp.save_pending(sender, did, st)
        log.info("[gate_resume] dialog %s → resume_engine_gate", did)
    except Exception as ex:  # noqa: BLE001 — best-effort, non blocca il turno
        log.warning("[gate_resume] inject failed: %r", ex)


# ── Upload-default faceless: separazione + descrizione VLM (§7.9, 1/7/2026) ──
# Lo score composito di find_images_indices (coseno BGE-M3 + 0.2·BM25) NON ha
# scala assoluta interpretabile — il BM25 può superare 1.0, quindi lo screenshot
# fuori-indice segna ~1.76 > la foto-scena Venezia self=1.0 (Roberto 1/7). La
# decisione «mostra foto simili?» usa perciò la SEPARAZIONE RELATIVA dei
# punteggi restituiti, non una soglia assoluta: spread=(max-min)/max. Sotto
# soglia = banda piatta = il vicino più prossimo è rumore (nessuna somiglianza
# genuina). Margini sui casi reali: screenshot spread≈0.0017 (piatto, ~30x sotto
# soglia); Venezia self=1.0 + coda spread≈0.7 (separato, ~14x sopra). Un cluster
# di quasi-duplicati identici (spread≈0) è dichiarato piatto per costruzione: la
# descrizione VLM resta comunque la risposta utile.
_FACELESS_FLAT_SPREAD = 0.05
_FACELESS_DESC_CAP = 600  # cap testo lead (descrizione VLM «ricca» per ricerca)


def _faceless_scores_are_flat(scores: list,
                              threshold: float = _FACELESS_FLAT_SPREAD) -> bool:
    """True se i punteggi non hanno separazione (banda piatta → nessun match
    genuino). len<2 → False: un singolo risultato non è una banda piatta."""
    xs = [float(s) for s in scores if isinstance(s, (int, float))]
    if len(xs) < 2:
        return False
    top = max(xs)
    if top <= 0:
        return True
    return (top - min(xs)) / top < threshold


def _recompose_faceless_upload(run: RunResult) -> None:
    """Post-processo DETERMINISTICO del path upload-default faceless (§7.9).

    (1) La risposta PARTE dalla descrizione VLM (sempre utile, anche a 0 match).
    (2) Le «foto simili» si mostrano SOLO se c'è separazione netta nei punteggi;
        banda piatta = rumore → entries/attachments/n_above_threshold azzerati
        (testo E gallery non mostrano foto casuali). Muta in-place lo step
        find_images_indices e `run.final_text`. No-op se manca describe_images o
        find_images_indices (il path a-volto non ha describe → non entra qui)."""
    from messages import get as _msg
    desc_step = next((s for s in run.steps
                      if getattr(s, "tool", "") == "describe_images"
                      and isinstance(s.result, dict)), None)
    find_step = next((s for s in run.steps
                      if getattr(s, "tool", "") == "find_images_indices"
                      and isinstance(s.result, dict)), None)
    if desc_step is None or find_step is None:
        return
    # Descrizione VLM (lead): query_text unito, o la prima description non vuota.
    desc = (desc_step.result.get("query_text") or "").strip()
    if not desc:
        for e in (desc_step.result.get("entries") or []):
            if isinstance(e, dict) and (e.get("description") or "").strip():
                desc = e["description"].strip()
                break
    if len(desc) > _FACELESS_DESC_CAP:
        desc = desc[:_FACELESS_DESC_CAP].rstrip() + "…"
    fres = find_step.result
    entries = fres.get("entries") or []
    scores = [e.get("score") for e in entries if isinstance(e, dict)]
    # Nessun match (ricerca vuota) O banda piatta (rumore) → «nessuna simile»:
    # in entrambi i casi testo E gallery non mostrano foto non-pertinenti.
    if not entries or _faceless_scores_are_flat(scores):
        fres["entries"] = []
        fres["attachments"] = []
        fres["n_above_threshold"] = 0
        tail = _msg("MSG_UPLOAD_NO_SIMILAR")
    else:
        tail = _msg("MSG_UPLOAD_SIMILAR_COUNT", n=len(entries))
    lead = _msg("MSG_UPLOAD_PHOTO_DESC", desc=desc) if desc else ""
    run.final_text = ((lead + "\n\n" + tail).strip() if lead else tail)


def run_turn(*, query: str, intent: Intent, catalog: list,
              invoke_executor_cb: Callable,
              llm_call_wise: Optional[Callable] = None,
              llm_call_fast: Optional[Callable] = None,
              vaglio_judge: Optional[Callable] = None,
              vaglio_guard: Optional[Callable] = None,
              remediate_args_cb: Optional[Callable] = None,
              runtime_ctx: Optional[dict] = None,
              seed_state: Optional[list] = None,
              turn_id: str = "",
              lang: str = "it",
              verbose: bool = False,
              progress=None) -> DispatchResult:
    """Entry point engine v2. Orchestrazione 4 layer.

    Returns:
      DispatchResult con final_text/kind + match_source per debug/telemetry.
    """
    t_start = time.time()
    # Difesa §2.8/§7.9: un turno foto-upload SENZA testo arriva con query=None
    # (caso reale 25/6 sonda upload_fallthrough). Coerci a "" SUBITO così i
    # pre-stadi (decontaminazione/pool) non sollevano su None e lo short-circuit
    # upload-default sotto può fidarsi che `query` sia una stringa.
    query = query or ""
    # De-contaminazione oggetti-clausola (v3, 19/6): a 7-8 clausole l'LLM ancora
    # una clausola all'oggetto di una vicina (foto→files dopo «file»). Corregge
    # via _OBJECT_HINTS (funzione canonica) PRIMA di tutto. Gated is_v3().
    from . import is_v3
    if is_v3():
        _decontaminate_clause_objects(intent, query)
        _fix_unroutable_verbs(intent, query, catalog)
    # gate-resume (20/6): sulla RIPRESA dopo approvazione (runtime_ctx
    # _gate_approved) il gate e' gia' consentito → togli la clausola
    # (get, approval) dall'intent cosi' il proposer compone la pipeline PULITA a
    # valle (find → send → write, variant A) senza il gate in mezzo, che
    # confonderebbe ordine/from_step. Universale §7.9: deterministico, no-op se
    # non e' una ripresa o non c'e' alcun gate nell'intent.
    if (runtime_ctx or {}).get("_gate_approved"):
        _acts0 = [a for a in (getattr(intent, "actions", None) or [])
                  if not (isinstance(a, dict)
                          and (a.get("verb") or "").lower() == "get"
                          and (a.get("object") or "").lower() == "approval")]
        try:
            intent.actions = _acts0
        except Exception:  # noqa: BLE001 — intent immutabile: best-effort
            pass
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

    # Seed-state uploads (ADR 0177 M1): con foto allegate (seed `@uploaded`)
    # garantisci i consumer-immagine nel pool così il proposer può instradarli;
    # il loro input reale (`reference_images`) è iniettato a valle dal
    # seed-wiring di Executor.run. Deterministico §7.9, no-op senza seed.
    if seed_state:
        _cat_names = catalog_names(catalog)
        for _img_tool in ("find_images_indices", "find_persons_indices"):
            if _img_tool in _cat_names and _img_tool not in pool_names:
                pool_names.append(_img_tool)

    executor = Executor(
        invoke_executor=invoke_executor_cb,
        llm_call_fast=llm_call_fast,
        vaglio_judge=vaglio_judge,
        vaglio_guard=vaglio_guard,
        seed_steps=seed_state,
        catalog=catalog,
    )

    # ── Undo SAFETY-CRITICAL (§4.5, §7.9) ────────────────────────────────
    # Query che INIZIA con un prefisso UNDO («annulla …», «undo …»,
    # «ripristina …») → undo_last_turn DETERMINISTICO, bypassa il routing LLM.
    # Bug live ec922ea1: «annulla ultima azione» → Aporia, perché il bypass
    # viveva SOLO nel fast_path di agent_runtime (saltato su resume/dialog
    # pendente) e l'engine — path vivo — non lo aveva. Mai lasciare il proposer
    # scegliere delete_* su un undo (turn 742b746d: delete_events distruttivo).
    # Prima di L0/L1: un undo non deve mai pescare un piano cachato.
    try:
        from fast_path import _undo_prefix_match, _normalize
        if (_undo_prefix_match(_normalize(query))
                and "undo_last_turn" in catalog_names(catalog)):
            from .types import Framework as _Fw, StepSpec as _St
            _undo_fw = _Fw(steps=[_St(tool="undo_last_turn", args={}),
                                  _St(tool="final_answer", args={})])
            run = executor.run(_undo_fw, query=query, runtime_ctx=runtime_ctx,
                               remediate_args_cb=remediate_args_cb, progress=progress)
            return DispatchResult(
                final_text=run.final_text, final_kind=run.final_kind,
                match_source="undo", framework_hash=run.framework_hash,
                elapsed_ms=int((time.time() - t_start) * 1000),
                run=run, framework=_undo_fw)
    except Exception as ex:  # noqa: BLE001 — best-effort, non blocca il turno
        log.warning("undo short-circuit noop (best-effort): %r", ex)

    # ── Upload SENZA testo → default DETERMINISTICO (§7.9, ADR 0177 M1) ───
    # Foto allegate (seed `@uploaded`) con query VUOTA/None: il proposer LLM non
    # ha istruzione da pianificare → ritorna None/solleva → il caller cadeva nel
    # PLANNER legacy (sonda `upload_fallthrough`, unico ingresso 25/6 query=None).
    # Risoluzione a DUE livelli, DETERMINISTICA (nessun LLM nel routing):
    #   1) similarità VOLTO (ArcFace) — find_images_indices sul seed @uploaded
    #      (seed-wiring auto-inietta reference_images via from_step=1). Risolve le
    #      foto di persone (il caso reale di un upload).
    #   2) se il volto NON risolve (foto senza volto: l'indice locale è solo-volto)
    #      e c'è describe_images → descrivi il CONTENUTO col VLM (descrizione
    #      RICCA) e cercala come testo: describe_images → find_images_indices(
    #      query_text=${step1.query_text}, idx="scene"). Ricerca per contenuto
    #      contro l'indice VLM → foto simili (o «niente di simile», sempre answer,
    #      mai un errore §2.8). UNIVERSALE (N foto). No-op se c'è testo o no seed.
    if seed_state and not (query or "").strip():
        try:
            _cat = catalog_names(catalog)
            _seed_is_upload = any(getattr(s, "tool", "") == "@uploaded"
                                  for s in seed_state)
            _face = next((t for t in ("find_images_indices",
                                      "find_persons_indices") if t in _cat), None)
            if _seed_is_upload and _face:
                from .types import Framework as _Fw, StepSpec as _St
                _fw = _Fw(steps=[_St(tool=_face, args={}),
                                 _St(tool="final_answer", args={})])
                run = executor.run(_fw, query=query, runtime_ctx=runtime_ctx,
                                   remediate_args_cb=remediate_args_cb,
                                   progress=progress)
                # Volto non risolto → descrivi + cerca per contenuto.
                if (run.final_kind != "answer"
                        and "describe_images" in _cat
                        and "find_images_indices" in _cat):
                    # NB: il seed @uploaded occupa step1 (riferito da from_step=1);
                    # describe_images è quindi step2 → query_text=${step2.query_text}.
                    _fw2 = _Fw(steps=[
                        _St(tool="describe_images", args={}),
                        _St(tool="find_images_indices",
                            args={"query_text": "${step2.query_text}",
                                  "idx": "scene"}),
                        _St(tool="final_answer", args={})])
                    run2 = executor.run(_fw2, query=query, runtime_ctx=runtime_ctx,
                                        remediate_args_cb=remediate_args_cb,
                                        progress=progress)
                    if run2.final_kind == "answer":
                        # Descrizione VLM in testa + foto simili solo se
                        # separazione netta (banda piatta = rumore, §7.9).
                        _recompose_faceless_upload(run2)
                        run, _fw = run2, _fw2
                return DispatchResult(
                    final_text=run.final_text, final_kind=run.final_kind,
                    match_source="upload_default",
                    framework_hash=run.framework_hash,
                    elapsed_ms=int((time.time() - t_start) * 1000),
                    run=run, framework=_fw)
        except Exception as ex:  # noqa: BLE001 — best-effort, non blocca il turno
            log.warning("upload-default short-circuit noop (best-effort): %r", ex)

    # ── Layer 0: Fastpath ────────────────────────────────────────────────
    # Seed-state (ADR 0177 M1): con seed (foto allegate) salta L0/L1 — un turno
    # con allegati è context-specific, deve ripianificare sul contenuto corrente
    # (parità col path legacy ADR 0092 che skippava il fast_path). Evita anche
    # di servire un piano cachato no-upload a un turno upload (e viceversa).
    if is_fastpath_enabled() and not seed_state:
        fp_hit = _fp.lookup(query)
        if fp_hit is not None:
            # Morte C1 a hit-time (§2.8): un piano che riferisce un executor
            # non più nel catalog (ritirato/rinominato/archiviato) NON va
            # eseguito (fallirebbe wrong_tool) né tenuto: delete +
            # fall-through a L1/L3, che ripianificano col catalog corrente;
            # il successo ri-crea il fastpath col piano nuovo (self-healing).
            _cat_names = catalog_names(catalog)
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
            # consent-gate (20/6): un piano cachato (gate-less, anche post
            # approvazione) NON deve postare in un turno SCHEDULATO senza
            # consenso → reinserisci il gate anche sull'hit L0. Stessa difesa
            # D3-B (guard deterministici sugli hit cache). No-op se non
            # schedulato / nessun send_*.
            fp_hit.framework = _insert_consent_gate_if_scheduled(
                fp_hit.framework, query, runtime_ctx)
            run = executor.run(fp_hit.framework, query=query,
                                runtime_ctx=runtime_ctx,
                                remediate_args_cb=remediate_args_cb,
                                progress=progress)
            _inject_gate_resume_if_paused(run, query, runtime_ctx)
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
    # Seed-state (ADR 0177 M1): salta anche L1 con seed (vedi L0 sopra).
    if is_autopath_enabled() and intent.is_complete() and not seed_state:
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
            # consent-gate (20/6): stessa difesa di L0 — reinserisci il gate
            # anche sull'hit L1 (autopath generalizzato) per i turni schedulati
            # outbound. No-op se non schedulato / nessun send_*.
            ap_hit.framework = _insert_consent_gate_if_scheduled(
                ap_hit.framework, query, runtime_ctx)
            run = executor.run(ap_hit.framework, query=query,
                                runtime_ctx=runtime_ctx,
                                remediate_args_cb=remediate_args_cb,
                                progress=progress)
            _inject_gate_resume_if_paused(run, query, runtime_ctx)
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
        llm_call=llm_call_wise, lang=lang, catalog=catalog,
        prior_steps=seed_state or ())
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
            exclude_tools=("get_inputs",), prior_steps=seed_state or ())
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
                llm_call=llm_call_wise, lang=lang, catalog=catalog,
                prior_steps=seed_state or ())
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
                llm_call=llm_call_wise, lang=lang, catalog=catalog,
                prior_steps=seed_state or ())
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
    # consent-gate (20/6): turno schedulato + pipeline outbound (send_*) →
    # inserisci get_approval prima del send (FIX 1 mette in pausa, on-approve
    # riprende pulito). Dopo l'ordinamento, prima dell'esecuzione.
    framework = _insert_consent_gate_if_scheduled(framework, query, runtime_ctx)

    # Execute
    run = executor.run(framework, query=query,
                        runtime_ctx=runtime_ctx,
                        remediate_args_cb=remediate_args_cb,
                        progress=progress)
    _inject_gate_resume_if_paused(run, query, runtime_ctx)

    # Record observation (per future feedback). Skip se non cacheabile
    # (single-executor / valore numerico baked): L1 non deve avere valori baked.
    # Seed-state (ADR 0177 M1): non cachare i turni con allegati (vedi L0/L1).
    if (turn_id and intent.is_complete() and not seed_state
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
        # §2.11 ERRORE-RUNTIME → FORM (interazione onesta, 25/6): un executor che
        # fallisce in modo DECIDIBILE-DALL'UTENTE emette nel result un segnale
        # strutturato `disambiguation` {prompt, options:[{value,label}], var?}.
        # Invece dell'errore secco (terminator) chiediamo con un form get_inputs.
        # Generale §7.9: il recovery NON conosce i casi — l'executor dichiara la
        # scelta, qui c'è UN solo traduttore. Gate METNOS_ERROR_FORM (default ON).
        _form = _error_disambiguation_form(run, query)
        if _form is not None:
            return DispatchResult(
                final_text="", final_kind="needs_inputs",
                match_source="error_disambiguation",
                framework_hash=run.framework_hash,
                elapsed_ms=int((time.time() - t_start) * 1000),
                run=run, framework=framework, error_class="missing_input",
                needs_inputs_obs=_form)
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
                    # Seed-state (ADR 0177 M1): turni con allegati non cacheati.
                    if not seed_state:
                        _maybe_record_fastpath(query, intent, framework_alt,
                                               run2, catalog=catalog)
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

    # Seed-state (ADR 0177 M1): turni con allegati (upload) non cacheati.
    if not seed_state:
        _maybe_record_fastpath(query, intent, framework, run, catalog=catalog)
    return DispatchResult(
        final_text=run.final_text, final_kind=run.final_kind,
        match_source="engine", framework_hash=run.framework_hash,
        elapsed_ms=int((time.time() - t_start) * 1000),
        run=run, framework=framework)
