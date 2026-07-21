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
    (Generalizzazione+re-bind dei valori in L1 = TODO Fable, fix completo.)

    ESENZIONE (1/7/2026): le chiavi CONTENT_ARG_KEYS/_GLOB_ARG_KEYS sono
    query-specific PER COSTRUZIONE (is_query_specific → 0a-only, mai 0b/L1
    champion) e il replay 0a ri-risolve gli slot (resolve_query_canonical_args):
    il loro numero non discrimina male. Senza esenzione, `time_window=
    "last-24h"` per «ultime 24 ore» bloccava la cache dell'INTERA classe
    «ultimi N giorni/ore/mesi» — progettata cacheabile 0a (fastpath.py, 12/6) —
    e ogni ripetizione ripagava L3. `delete_tasks(ids=[42])` resta bloccato
    (chiave non-content)."""
    # Tool context-dependent (undo/get_inputs/get_approval): il replay fuori
    # dal turno d'origine è scorretto — L0 li rifiuta già in record_success,
    # qui si copre anche il record_observation L1 (un champion col consent-gate
    # baked verrebbe servito a turni interattivi che non lo richiedono).
    if any((getattr(s, "tool", "") or "") in _fp.NON_CACHEABLE_TOOLS
           for s in (getattr(framework, "steps", []) or [])):
        return False
    from .executor import CONTENT_ARG_KEYS, _GLOB_ARG_KEYS
    _exempt = CONTENT_ARG_KEYS | _GLOB_ARG_KEYS
    qnums = set(re.findall(r"\d+", query or ""))
    if qnums:
        for s in (getattr(framework, "steps", []) or []):
            for k, v in (getattr(s, "args", {}) or {}).items():
                if k in ("from_step", "from_steps") or k in _exempt:
                    continue
                if set(re.findall(r"\d+", str(v))) & qnums:
                    return False
    # Piani MATERIALIZZATI (bug live anom-1/anom-2, 7/7): uno step MUTANTE
    # con una LISTA literal i cui valori NON compaiono nella query = valori
    # risolti dai RESULT del turno (from_step già materializzato) → il
    # replay 0b canonicalizza quei literal su path nuovi e serve piani
    # incoerenti (delete di 1 file su 4; piano «file e directory» a una
    # query «solo file»). Cacheable SOLO se ogni elemento è nella query
    # (§4.2 caso degenere N=1 literal). Gemello della policy «L1 no baked».
    from pipeline_effects import MUTATING_TOOL_PREFIXES as _MUT_PFX
    _q = (query or "").lower()
    for s in (getattr(framework, "steps", []) or []):
        tool = getattr(s, "tool", "") or ""
        if not any(tool.startswith(p) for p in _MUT_PFX):
            continue
        for k, v in (getattr(s, "args", {}) or {}).items():
            # Esenti SOLO i glob (pattern generici riusabili): i content-key
            # (paths, names…) NON sono esenti QUI — negli step mutanti sono
            # proprio i literal materializzati del bug (l'esenzione content
            # vale per il check numerico, dove il replay ri-risolve gli slot).
            if k in _GLOB_ARG_KEYS or not isinstance(v, list) or not v:
                continue
            if not all(isinstance(x, str) for x in v):
                continue
            # Confine: SOLO elementi PATH-like (con separatore / o \) —
            # il bug è sui path filesystem materializzati; gli slug/id
            # risolti (es. chosen_slugs=[mario-rossi]) restano cacheable
            # per policy (test_fastpath_efficacy) con la garanzia hard a
            # serve-time (_mutating_args_grounded). Wildcard = pattern
            # generico riusabile, esente per valore.
            if any(("/" in x or "\\" in x)
                   and (not any(c in x for c in "*?"))
                   # Destinazione create-only unica per esecuzione: non è un
                   # path materializzato da un result precedente. Il token
                   # viene risolto JIT dal runtime corrente, quindi il replay
                   # exact-match non può sovrascrivere il run precedente.
                   and "${RUNTIME:turn_id}" not in x
                   and x.lower() not in _q for x in v):
                return False
    return True


def _leg_committed_mutations(run) -> list[str]:
    """Tool mutanti già COMMITTATI nel leg fallito (anti-doppia-esecuzione,
    2/7/2026). Delega a pipeline_effects.committed_mutations (SoT §7.9);
    fallback conservativo: ogni mutante con ok=True conta come committato."""
    steps = getattr(run, "steps", None) or []
    try:
        from pipeline_effects import committed_mutations
        return committed_mutations(steps)
    except Exception:
        prefixes = ("delete_", "move_", "send_", "write_",
                    "set_", "create_", "change_", "share_", "render_")
        return [s.tool for s in steps
                if getattr(s, "ok", False)
                and getattr(s, "kind", "live") == "live"
                and any((s.tool or "").startswith(p) for p in prefixes)]


def _ungrounded_mutating_args(framework, query) -> list[tuple[str, str, str]]:
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
    ungrounded: list[tuple[str, str, str]] = []
    path_keys = frozenset({
        "path", "paths", "dest", "destination", "output_path",
    })
    for s in (getattr(framework, "steps", []) or []):
        tool = getattr(s, "tool", "") or ""
        if not any(tool.startswith(p) for p in MUTATING_TOOL_PREFIXES):
            continue
        for k, v in (getattr(s, "args", {}) or {}).items():
            if k in ("from_step", "from_steps"):
                continue
            vs = str(v)
            # Cartella create-only per-turno: il segmento che contiene il
            # turn_id è generato dal runtime e garantisce un target nuovo. Va
            # ignorato come discriminante, mentre la BASE letterale resta nel
            # controllo sottostante (Documenti/A vs Documenti/B). Scope
            # deliberatamente stretto: solo create_* e soli arg path-like;
            # delete/move/send/update non ricevono alcuna esenzione.
            if (tool.startswith("create_") and k in path_keys
                    and "${RUNTIME:turn_id}" in vs):
                vs = re.sub(
                    r"[^/\\\s]*\$\{RUNTIME:turn_id\}[^/\\\s]*",
                    " ", vs)
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
                    ungrounded.append((tool, str(k), tok))
    return ungrounded


def _mutating_args_grounded(framework, query) -> bool:
    """True se tutti i discriminanti di ogni step mutante sono nella query.

    La policy fail-closed è implementata da ``_ungrounded_mutating_args``;
    questa forma booleana preserva l'API usata dai test e dai chiamanti.
    """
    return not _ungrounded_mutating_args(framework, query)


def _run_is_cacheworthy(run: RunResult) -> bool:
    """No-cache-on-error (Roberto 9/7): un run va in cache (L0 record / L1
    observation) SOLO se efficace — `final_kind=answer`, non abortito, e
    nessun mutante eseguito a 0 effetto reale. Un turno in errore/degenere
    cachato si auto-perpetua (ri-servito in millisecondi bypassando il
    proposer) — è una delle 3 modalità di fallimento L0/L1 viste live 9/7.
    Best-effort sul check mutazioni (fallisce → non blocca, come L0)."""
    if run is None or run.final_kind != "answer" or run.aborted_reason:
        return False
    if any(not getattr(s, "ok", True) for s in (run.steps or [])):
        return False  # step fallito nel mezzo (final honesto ma piano rotto)
    try:
        from pipeline_effects import ineffective_mutations
        if ineffective_mutations(run.steps):
            return False
    except Exception as ex:  # noqa: BLE001 — best-effort ma non muto (§2.8)
        log.warning("cacheworthy: efficacy-check fallito (permetto): %r", ex)
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
    if not _run_is_cacheworthy(run):
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
                                   origin=origin, catalog=catalog)
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
    INSERISCE con `fields` derivati dalla clausola «estrai X e Y» quando
    disponibili; (2) PRESENTE ma SENZA `fields` → li RIEMPIE quando derivabili.
    In assenza di schema esplicito l'executor usa la propria inferenza bounded.
    v3-gated, mai eccezioni."""
    try:
        from . import is_v3
        if not is_v3():
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        if not steps:
            return framework
        import naming_grammar as _ng
        from compound_decomposer import (PRODUCER_VERBS as _PV,
                                         derive_extract_fields,
                                         derive_sink_fields)

        def _verb(s):
            nc = _ng.parse_name(getattr(s, "tool", "") or "")
            return nc.verb if nc else ""
        actions = getattr(intent, "actions", None) or []
        _has_intent_extract = any(isinstance(a, dict)
                                  and (a.get("verb") or "") == "extract"
                                  for a in actions)
        # STRUTTURA (intent flaky-safe, turn bb977a14): un sink columns-declaring
        # che consuma un CONTENT-reader (Doc/pdf/mail/url: testo GREZZO) SENZA
        # extract → i record vanno estratti nelle colonne dichiarate. L'intent
        # DROPPA spesso la clausola-extract IMPLICITA («crea un foglio con i dati:
        # X,Y,Z» non ha un verbo «estrai»); la STRUTTURA la impone. NON per
        # list_/find_/get_files (metadata gia' strutturata) ne' read_*_spreadsheet
        # (righe, FIX-4 passthrough). Richiede fields derivabili (schema-marker).
        _CONTENT_READERS = {"read_files", "read_files_doc", "read_files_pdf",
                            "read_files_html", "read_messages", "read_urls",
                            "read_urls_html", "get_urls", "read_sites"}
        _derived_fields = derive_extract_fields(query)
        _sink_fields = derive_sink_fields(query)
        _spreadsheet_sinks = [
            s for s in steps
            if _verb(s) in ("create", "write")
            and (getattr(s, "tool", "") or "").endswith("_spreadsheet")
        ]
        # Uno spreadsheet dichiara il proprio schema attraverso `columns`.
        # Se il planner ha preservato la richiesta naturale ma non l'argomento,
        # usa gli stessi campi che guideranno l'extract intermedio.
        if _sink_fields or _derived_fields:
            for _sink in _spreadsheet_sinks:
                _sa = dict(getattr(_sink, "args", None) or {})
                if not _sa.get("columns"):
                    _sa["columns"] = list(_sink_fields or _derived_fields)
                    _sink.args = _sa
        _need_by_structure = False
        if not _has_intent_extract:
            _has_reader = any((s.tool or "") in _CONTENT_READERS for s in steps)
            _has_col_sink = any(
                _verb(s) in ("create", "write")
                and (getattr(s, "args", None) or {}).get("columns")
                for s in steps)
            if _has_reader and _has_col_sink and _derived_fields:
                _need_by_structure = True
                log.info("[ensure_extract] trigger STRUTTURALE (content-reader → "
                         "columns-sink, intent senza extract)")
        if not _has_intent_extract and not _need_by_structure:
            return framework
        from .types import StepSpec
        # extract_entries GIA' presente: il proposer a volte lo emette SENZA
        # `fields`. Preferisci la derivazione deterministica quando possibile;
        # altrimenti l'executor applica la propria inferenza bounded.
        # Riempi `fields` DETERMINISTICAMENTE dalla clausola «estrai X e Y». Non
        # ne inseriamo un secondo. Se la query e' opaca → lascia com'e' (errore-
        # guida onesto a valle).
        # SINK BULK a valle (create/write con `columns`) → l'extract deve prendere
        # TUTTI i record, non i primi 20 (default): alza max_per_text (turn 8167889d).
        _bulk_sink = any(_verb(s) in ("create", "write")
                         and (getattr(s, "args", None) or {}).get("columns")
                         for s in steps)

        def _producer_tool(extract_args: dict) -> str:
            pos = extract_args.get("from_step")
            if isinstance(pos, str) and pos.isdigit():
                pos = int(pos)
            if isinstance(pos, int) and 1 <= pos <= len(steps):
                return (getattr(steps[pos - 1], "tool", "") or "")
            return ""

        def _apply_bulk_limits(extract_args: dict, producer: str) -> None:
            """Collection readers are N texts, not one 500-record document.

            The old bulk rule assigned ``max_per_text=500`` to every email.
            Besides wasting prompt/output budget, that left the independent
            source loop in its slowest mode.  Keep the historical document
            limit, but use bounded per-source extraction and structured
            batching for mail/calendar collections.  Link traversal remains
            opt-in because it has different semantics and cannot be batched.
            """
            if not _bulk_sink:
                return
            if producer not in {"read_messages", "read_events"}:
                extract_args.setdefault("max_per_text", _BULK_EXTRACT_CAP)
                return
            extract_args.setdefault("max_per_text", 20)
            extract_args.setdefault("max_total", _BULK_EXTRACT_CAP)
            extract_args.setdefault("max_sources", _BULK_EXTRACT_CAP)
            requests_links = bool(re.search(
                r"\b(?:apri|segui|visita|open|follow|visit)\w*"
                r"(?:\W+\w+){0,3}\W+(?:link|collegament\w*|url)\b",
                query or "", re.IGNORECASE))
            if not requests_links:
                extract_args.setdefault("drill_down", False)
                extract_args.setdefault("batch_size", 8)

        existing = next((s for s in steps
                         if (getattr(s, "tool", "") or "") == "extract_entries"),
                        None)
        if existing is not None:
            ea = getattr(existing, "args", None) or {}
            if not ea.get("fields"):
                _ef = _derived_fields
                if _ef:
                    ea["fields"] = _ef
                    log.info("[ensure_extract] fields riempiti su extract_entries "
                             "esistente: %s", _ef)
            if not ea.get("instruction") and query:
                ea["instruction"] = query
            if _bulk_sink:
                _apply_bulk_limits(ea, _producer_tool(ea))
                log.info("[ensure_extract] policy bulk su extract esistente: "
                         "producer=%s per_text=%s sources=%s batch=%s",
                         _producer_tool(ea), ea.get("max_per_text"),
                         ea.get("max_sources"), ea.get("batch_size"))
            existing.args = ea
            return framework

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
        # Deriva `fields` DETERMINISTICAMENTE dalla clausola «estrai X e Y»
        # quando sono espliciti. Se la query non li espone, l'executor inferisce
        # internamente un piccolo schema: il guard non conosce il dominio.
        ins_args = {"from_step": prod_1b}
        _fields = _derived_fields
        if _fields:
            ins_args["fields"] = _fields
        if query:
            ins_args["instruction"] = query
        if _bulk_sink:
            _apply_bulk_limits(ins_args, getattr(steps[pi], "tool", "") or "")
        steps.insert(pi + 1, StepSpec(tool="extract_entries", args=ins_args))
        framework.steps = steps
        log.info("[ensure_extract] extract_entries inserito @1b=%d (dopo "
                 "produttore @%d) fields=%s", k, prod_1b, _fields or "—")
        return framework
    except Exception as ex:
        log.warning("ensure_extract_clause noop (best-effort): %r", ex)
        return framework


def _ensure_extracted_period_scope(framework: Framework, intent, query: str,
                                   catalog: Optional[list]) -> Framework:
    """Filtra deterministicamente i record estratti sugli anni espliciti.

    L'extract LLM struttura il testo, ma non gli affidiamo il rispetto di un
    vincolo esatto come «2026». Se il risultato dichiara un campo data/anno e
    la query contiene uno o piu' anni, inserisce `filter_entries` subito dopo
    `extract_entries` e ricabla i consumer. Idempotente e domain-agnostic.
    """
    try:
        years = list(dict.fromkeys(re.findall(
            r"(?<!\d)((?:19|20)\d{2})(?!\d)", query or "")))
        if not years:
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        ex_idx = next((i for i, step in enumerate(steps)
                       if (getattr(step, "tool", "") or "") ==
                       "extract_entries"), -1)
        if ex_idx < 0:
            return framework
        fields = (getattr(steps[ex_idx], "args", None) or {}).get("fields")
        if not isinstance(fields, list):
            return framework
        year_field = next((field for field in fields
                           if isinstance(field, str) and re.search(
                               r"(^|[_\s])(year|anno)([_\s]|$)",
                               field, re.IGNORECASE)), None)
        date_field = next((field for field in fields
                           if isinstance(field, str) and re.search(
                               r"(^|[_\s])(date|data|scadenza|due|deadline|"
                               r"emiss(?:ione)?|issue)([_\s]|$)",
                               field, re.IGNORECASE)), None)
        scope_field = year_field or date_field
        if not scope_field:
            return framework
        extract_pos = ex_idx + 1
        filter_args = ({"from_step": extract_pos,
                        "where_field": scope_field,
                        "where_in": years}
                       if year_field else
                       {"from_step": extract_pos,
                        "where_field": scope_field,
                        "where_regex": "^(?:" + "|".join(years) + ")-"})
        if any((getattr(step, "tool", "") or "") == "filter_entries"
               and (getattr(step, "args", None) or {}).get("from_step") ==
               extract_pos
               and (getattr(step, "args", None) or {}).get("where_field") ==
               scope_field
               and ((getattr(step, "args", None) or {}).get("where_in") == years
                    or (getattr(step, "args", None) or {}).get("where_regex") ==
                    filter_args.get("where_regex"))
               for step in steps):
            return framework

        filter_pos = extract_pos + 1
        idx_map = {i: (i if i < extract_pos else
                       filter_pos if i == extract_pos else i + 1)
                   for i in range(1, len(steps) + 1)}
        new_steps = [StepSpec(
            tool=step.tool,
            args=_remap_step_refs(dict(getattr(step, "args", {}) or {}),
                                  idx_map),
            if_prev_entries_nonempty=step.if_prev_entries_nonempty,
        ) for step in steps]
        # L'extract resta nella sua posizione: il suo input punta al producer,
        # non al filtro appena inserito. Solo i riferimenti a valle devono
        # consumare il sottoinsieme filtrato.
        new_steps[ex_idx].args = dict(getattr(steps[ex_idx], "args", {}) or {})
        new_steps.insert(ex_idx + 1, StepSpec(
            tool="filter_entries", args=filter_args))
        log.info("[period_scope] filter_entries inserito dopo extract: %s=%s",
                 scope_field, years)
        return Framework(
            steps=new_steps,
            fillers=getattr(framework, "fillers", {}) or {},
            final_message=_remap_step_refs(
                getattr(framework, "final_message", ""), idx_map),
        )
    except Exception as ex:
        log.warning("ensure_extracted_period_scope noop (best-effort): %r", ex)
        return framework


def _fs_container_of(path: str) -> Optional[str]:
    """Cartella-contenitore di un path glob/wildcard, OS-agnostica (posix e
    Windows). «/tmp/x/*»→«/tmp/x»; «C:\\d\\Downloads\\*»→«C:\\d\\Downloads».
    None se il path non è un glob di primo livello (niente wildcard nell'ultimo
    segmento)."""
    if not isinstance(path, str) or not path:
        return None
    import ntpath
    import posixpath
    mod = ntpath if ("\\" in path) else posixpath
    parent, leaf = mod.split(path)
    if any(w in leaf for w in ("*", "?", "[")):
        return parent.rstrip("\\/") or parent
    return None


def _norm_dir(path: str) -> str:
    """Normalizzazione leggera per confronto contenitore↔target: strip di
    separatori/wildcard finali, OS-agnostica. Non risolve symlink (i path
    remoti non sono stat-abili dal server)."""
    if not isinstance(path, str):
        return ""
    return path.rstrip("*").rstrip("\\/") or path


def _scope_dirs_clause_to_contents(framework: Framework, intent, query: str,
                                   catalog: Optional[list]) -> Framework:
    """§2.9 (decisione Roberto 7/7): «cancella i file E le directory NELLA
    cartella X» scopa la clausola dirs ai CONTENUTI di X, non al contenitore.

    Il proposer emette `delete_dirs(paths=[X], force=true)` dove X è la STESSA
    cartella-contenitore della clausola file fratella (`delete_files(paths=[X/*])`
    o un produttore `find_files(base_path=X)`) → rimuove X RICORSIVAMENTE:
    sparisce anche X e i file annidati (over-deletion, turno reale e6259280).
    Fix: inserisci `find_dirs(base_path=X)` e ripunta `delete_dirs` a
    `from_step` → colpisce le SOTTODIRECTORY di X, non X. X sopravvive. `force`
    preservato (l'utente vuole le directory rimosse, non svuotate).

    DISCRIMINANTE (conservativo): scatta SOLO se `delete_dirs` bersaglia
    ESATTAMENTE la cartella che una clausola FILE fratella tratta da contenitore.
    «cancella la cartella X» (senza clausola file) → nessun contenitore-candidato
    → NON si tocca: rimuovere X è ciò che l'utente chiede. v3-gated, best-effort."""
    try:
        from . import is_v3
        if not is_v3():
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        if not steps:
            return framework
        names = catalog_names(catalog)
        if "find_dirs" not in names or "delete_dirs" not in names:
            return framework  # catalogo privo dei tool → non riscrivere

        def _args(s):
            return getattr(s, "args", None) or {}

        # Contenitori-candidati: cartelle che una clausola FILE tratta da
        # contenitore (find_files/delete_files base_path, o glob paths X/*).
        containers = set()
        for s in steps:
            tool = getattr(s, "tool", "") or ""
            a = _args(s)
            if tool in ("find_files", "delete_files"):
                bp = a.get("base_path")
                if isinstance(bp, str) and bp:
                    containers.add(_norm_dir(bp))
                for p in (a.get("paths") or []):
                    c = _fs_container_of(p)
                    if c:
                        containers.add(_norm_dir(c))
        if not containers:
            return framework

        # delete_dirs che bersaglia ESATTAMENTE un contenitore (paths=[X], niente
        # wildcard, niente from_step: target esplicito = la cartella-contenitore).
        from .types import StepSpec
        rewrote = False
        for dd in list(steps):
            if (getattr(dd, "tool", "") or "") != "delete_dirs":
                continue
            a = _args(dd)
            if a.get("from_step") is not None:
                continue  # già scopato a un produttore (non è il caso rotto)
            paths = a.get("paths")
            if not (isinstance(paths, list) and len(paths) == 1
                    and isinstance(paths[0], str)):
                continue
            if any(w in paths[0] for w in ("*", "?", "[")):
                continue  # glob esplicito = intento diverso, non toccare
            target = _norm_dir(paths[0])
            if target not in containers:
                continue  # bersaglia una sottodir NOMINATA, non il contenitore
            # Nessuno step consuma questo delete_dirs (è terminale): sicuro
            # spostarlo in coda senza rinumerare i from_step esistenti.
            my_idx = steps.index(dd) + 1  # 1-based
            if any(isinstance(_args(s).get("from_step"), int)
                   and _args(s)["from_step"] == my_idx for s in steps):
                continue
            force = a.get("force")
            body = [s for s in steps if s is not dd
                    and (getattr(s, "tool", "") or "") != "final_answer"]
            finals = [s for s in steps if (getattr(s, "tool", "") or "")
                      == "final_answer"]
            body.append(StepSpec(tool="find_dirs", args={"base_path": target}))
            fd_idx = len(body)  # 1-based posizione di find_dirs nel piano finale
            nd_args: dict = {"from_step": fd_idx}
            if force is not None:
                nd_args["force"] = force
            body.append(StepSpec(tool="delete_dirs", args=nd_args))
            steps = body + finals
            rewrote = True
            log.info("[scope_dirs] delete_dirs(paths=[%s]) → find_dirs(base=%s)"
                     "→delete_dirs(from_step=%d): scopato ai contenuti, X resta",
                     target, target, fd_idx)
        if rewrote:
            framework.steps = steps
        return framework
    except Exception as ex:
        log.warning("scope_dirs_clause noop (best-effort): %r", ex)
        return framework


def _ensure_health_arg(framework: Framework, query: str,
                       catalog: Optional[list]) -> Framework:
    """§7.9 (turn b66ec6f3 «ip metos server»): query HARDWARE/STATUS (lessici
    `system.status_query` o `health.section_focus`+`machine.reference`) con
    step get_processes SENZA include_health → il proposer a volte lo omette e
    la risposta esce senza rete/gpu/sistema («non contiene informazioni di
    rete»). Forza include_health=true: additivo (aggiunge dati, non ne toglie),
    il focus per-sezione seleziona poi la parte pertinente. Best-effort."""
    try:
        steps = getattr(framework, "steps", None) or []
        if not query:
            return framework
        hw = _dl_match("system.status_query", query)
        if not hw and _dl_match("machine.reference", query):
            try:
                import detection_lexicon as _dl
                fmap = _dl.mapping("health.section_focus") or {}
                ql = query.lower()
                hw = any(_dl.match_any(f, ql) for f in fmap.values())
            except Exception:  # noqa: BLE001
                hw = False
        # Compound file+health (turn ddd828a6, 20/7): l'align per oggetto può
        # trasformare il corretto `get_processes(include_health=true)` in
        # `get_files(include_health=true)` perché l'intent primario è files.
        # `include_health` NON appartiene al contratto get_files: è quindi una
        # prova strutturale, non un'euristica. Ripristina il producer health e
        # scarta gli args file-specific ormai incoerenti. Copre anche il caso
        # get_files vuoto quando un find_files separato soddisfa già i file.
        names = catalog_names(catalog)
        has_file_producer = any(
            (getattr(s, "tool", "") or "") in {"find_files", "list_dirs"}
            for s in steps)
        for s in steps:
            tool = (getattr(s, "tool", "") or "")
            a = getattr(s, "args", None)
            a = a if isinstance(a, dict) else {}
            wrong_health_contract = tool != "get_processes" and bool(
                a.get("include_health"))
            empty_get_files = (bool(hw) and tool == "get_files"
                               and has_file_producer
                               and not any(a.get(k) for k in
                                           ("entries", "paths", "from_step")))
            if (wrong_health_contract or empty_get_files) \
                    and "get_processes" in names:
                runtime_args = {k: v for k, v in a.items()
                                if str(k).startswith("_")}
                runtime_args["include_health"] = True
                runtime_args["top"] = 1
                s.tool = "get_processes"
                s.args = runtime_args
                log.info("[health_arg §7.9] %s incompatibile → "
                         "get_processes(include_health=true)", tool)
        if not hw and not any(
                (getattr(s, "tool", "") or "") == "get_processes"
                and bool((getattr(s, "args", None) or {}).get("include_health"))
                for s in steps):
            return framework
        for s in steps:
            if (getattr(s, "tool", "") or "") == "get_processes":
                a = getattr(s, "args", None)
                if not isinstance(a, dict):
                    a = {}
                    s.args = a
                if not a.get("include_health"):
                    a["include_health"] = True
                    log.info("[health_arg §7.9] get_processes: include_health "
                             "forzato (query hardware/status)")
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("ensure_health_arg noop (best-effort): %r", ex)
        return framework


def _normalize_result_folder_exclusion(framework: Framework, query: str,
                                       catalog: Optional[list]) -> Framework:
    """Rende effettiva l'esclusione degli output `Risultati_Metnos_*`.

    Il proposer può applicare il predicato a `name` (che contiene solo il nome
    file) oppure alterare il token in `Risultati_Menos_*`: entrambi lasciano
    rientrare gli XLSX dei run precedenti. Quando la query NOMINA quella famiglia
    e usa una forma negativa, il solo predicato corretto è sul `path` completo.
    Il regex è lo stesso del workflow documentale canonico ed è idempotente.
    """
    try:
        import unicodedata

        folded = unicodedata.normalize("NFKD", query or "").casefold()
        folded = "".join(ch for ch in folded
                         if not unicodedata.combining(ch))
        mentions_results = bool(re.search(
            r"risultati[_\s-]*metnos(?:[_\s-]*\*)?", folded))
        excludes = any(token in folded for token in (
            "esclud", "senza includ", "non includ", "exclude", "excluding",
            "without includ"))
        if not (mentions_results and excludes):
            return framework
        steps = getattr(framework, "steps", None) or []
        canonical = r"^(?!.*[\\/]Risultati_Metnos_[^\\/]+(?:[\\/]|$)).*$"
        last_file_producer = None
        changed = False
        for idx, step in enumerate(steps, 1):
            tool = (getattr(step, "tool", "") or "")
            if tool in {"find_files", "list_dirs"}:
                last_file_producer = idx
                continue
            if tool != "filter_entries" or last_file_producer is None:
                continue
            args = getattr(step, "args", None)
            args = args if isinstance(args, dict) else {}
            if args.get("where_field") != "path" \
                    or args.get("where_regex") != canonical \
                    or "name_regex" in args:
                args.pop("name_regex", None)
                args["where_field"] = "path"
                args["where_regex"] = canonical
                args["from_step"] = last_file_producer
                step.args = args
                changed = True
        if changed:
            log.info("[result_scope §7.9] esclusione Risultati_Metnos_* "
                     "normalizzata sul path completo")
        return framework
    except Exception as ex:  # noqa: BLE001 — best effort
        log.warning("normalize_result_folder_exclusion noop: %r", ex)
        return framework


def _enrich_move_source_dir(framework: Framework, query: str,
                            catalog: Optional[list]) -> Framework:
    """§7.9 (follow-up move-enumeration): «sposta i file DA una cartella X a Y».

    Il proposer passa la CARTELLA X come singola dir-entry a move_files
    (`entries=[{path:X}]`) → il safety-net la rifiuta (è una directory, serve
    allow_dirs) e 0 file si spostano. Ma l'utente vuole i FILE DENTRO X: serve un
    produttore. Fix: inserisci `find_files(base_path=X)` prima del move e ripunta
    move a `from_step`.

    DISCRIMINANTE (conservativo, solo move_files — mai delete): scatta SOLO se la
    query matcha `fs.files_in_folder` («i file da/in <cartella>», lessico IT+EN) E
    la sorgente del move è una SINGOLA path bare (no glob) senza from_step.
    «sposta la cartella X» (senza «file») NON matcha → X si sposta com'è."""
    try:
        from . import is_v3
        if not is_v3():
            return framework
        if not (query and _dl_match("fs.files_in_folder", query)):
            return framework
        names = catalog_names(catalog)
        if "find_files" not in names:
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        from .types import StepSpec
        rewrote = False
        for mv in list(steps):
            if (getattr(mv, "tool", "") or "") != "move_files":
                continue
            a = getattr(mv, "args", None) or {}
            if a.get("from_step") is not None:
                continue  # già una catena produttore→move
            src = None
            ents = a.get("entries")
            if (isinstance(ents, list) and len(ents) == 1
                    and isinstance(ents[0], dict)):
                src = ents[0].get("path")
            if src is None:
                ps = a.get("paths")
                if isinstance(ps, list) and len(ps) == 1 and isinstance(ps[0], str):
                    src = ps[0]
            if not (isinstance(src, str) and src.strip()):
                continue
            if any(w in src for w in ("*", "?", "[")):
                continue  # glob esplicito = intento diverso, non toccare
            my_idx = steps.index(mv)              # 0-based
            old_mv_1b = my_idx + 1                # 1-based del move PRIMA dell'insert
            steps.insert(my_idx, StepSpec(tool="find_files",
                                          args={"base_path": src}))
            # renumber: ogni from_step >= old_mv_1b (che puntava a move o oltre)
            # slitta di 1 per l'inserimento. Il move stesso lo settiamo dopo.
            for s in steps:
                if s is mv:
                    continue
                sfs = (getattr(s, "args", None) or {}).get("from_step")
                if isinstance(sfs, int) and sfs >= old_mv_1b:
                    s.args["from_step"] = sfs + 1
            mv.args = {k: v for k, v in a.items()
                       if k not in ("entries", "paths")}
            mv.args["from_step"] = old_mv_1b      # = pos 1-based del find_files
            rewrote = True
            log.info("[move_enum §7.9] move_files(dir=%s) → find_files(base=%s)"
                     "→move_files(from_step=%d): enumera i file", src, src,
                     old_mv_1b)
        if rewrote:
            framework.steps = steps
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("enrich_move_source_dir noop (best-effort): %r", ex)
        return framework


# Fratelli-FILESYSTEM (§2.2): `files` e `dirs` sono facce dello stesso dominio
# — «elenca i FILE della cartella X» si serve con list_dirs (container enum).
# Un intent-object `files` NON delegittima uno step `dirs` (e viceversa):
# senza, l'align DEMOLIVA il piano corretto cachato (list_dirs → get/find_files
# a seconda dell'ordine-hash del set, turni 2cd8862a/68f28b01) — stesso
# principio del filesystem-siblings in route_disambiguation.
_FS_SIBLING_OBJECTS = frozenset({"files", "dirs"})


def _fs_equivalent(step_obj: str, intent_objs) -> bool:
    """True se `step_obj` soddisfa uno degli `intent_objs` via l'equivalenza
    filesystem files↔dirs, o via il CARRIER §2.2: un producer files/dirs
    soddisfa un intent images/texts — le ops generiche per path NON duplicano
    `files` (turn a4f1f12c: «carica le foto di /tmp/dir» → il producer
    legittimo è find_files sulla cartella; la guard non deve riscriverlo in
    find_images_indices, che vuole un criterio semantico, non un path)."""
    from vocab import FILE_CARRIER_OBJECTS
    return (step_obj in _FS_SIBLING_OBJECTS
            and any(o in _FS_SIBLING_OBJECTS or o in FILE_CARRIER_OBJECTS
                    for o in (intent_objs or ())))


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
        # MONO-intent (9/7, turn «stato del server»→get_proposals): l'extractor
        # popola `actions` SOLO sui compound → il guard era NO-OP su tutti i
        # turni mono, proprio dove il proposer locale sbaglia più spesso il
        # fratello («come sta il server»→read_urls_html/wttr.in!). Sintetizza
        # la singola action dal verb/object top-level: stesso contratto.
        if not actions:
            _v = (getattr(intent, "verb", "") or "").lower()
            _o = (getattr(intent, "object", "") or "").lower()
            if _v and _o:
                actions = [{"verb": _v, "object": _o}]
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
            # `get_now` is the canonical producer for a date/time scalar.  In
            # a compound file+health query the intent extractor also emits
            # `get processes`; treating `now` as a foreign object would then
            # rewrite the explicit get_now step to get_processes.  Preserve
            # this distinct scalar producer so normalization remains
            # composable across domains.
            if (nc.verb == "get" and nc.obj == "now"
                    and "numbers" in (intent_objs or [])):
                continue
            if not intent_objs or nc.obj in intent_objs \
                    or _fs_equivalent(nc.obj, intent_objs):
                continue  # verbo non decomposto, o oggetto gia' corretto/equivalente
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
                    if (tool == "get_now" and "numbers" in all_objs):
                        # get_now is a valid scalar producer for the numbers
                        # clause; it is not a foreign filesystem/health
                        # producer to be replaced by get_processes.
                        continue
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
    # Famiglia SCRITTORI-FILE interscambiabile (create/write) — OBJECT-scoped
    # (FIX-5, turn 697d1d08 «...metti i path in uno spreadsheet»): l'intent
    # decompone (write,files) ma il proposer compone create_files_spreadsheet
    # (create,files) → `write` resta 'droppato' → enforce appende un write_files
    # SPURIO. Col guard anti-clobber quel write ora ERRA (§2.8: foglio creato ma
    # turno a errore). create_<obj> e write_<obj> dello STESSO object sono lo
    # STESSO sink: un create copre una clausola write e viceversa. NON
    # object-blind (create_events != write_files): confronto per-object dei
    # CONTEGGI, cosi' «crea un doc E scrivi un csv» (2 clausole files) resta
    # scoperto se il piano ne realizza 1. §7.9 deterministico.
    # §5 mail: «cancellare mail» = move_messages(Trash) — il rewrite di
    # `_route_mail_delete_to_trash` SODDISFA la clausola delete. Senza questo,
    # al giro DOPO (hit cache, ADR 0174) enforce ri-appendeva un delete
    # spurio (che derive risolveva pure male — T4 5/7). Scoped: solo se
    # l'intent chiede delete su MESSAGES e un move_messages è nel piano.
    if "delete" in dropped and any(
            (s.tool or "") == "move_messages" for s in framework.steps):
        _del_objs = {(a.get("object") or "") for a in
                     (getattr(intent, "actions", None) or [])
                     if isinstance(a, dict) and a.get("verb") == "delete"}
        if _del_objs <= {"messages", "entries", ""}:
            dropped.discard("delete")
    _WRITERS = {"create", "write"}
    if _WRITERS & dropped:
        import naming_grammar as _ng
        from collections import Counter as _Counter
        _acts = [a for a in (getattr(intent, "actions", None) or [])
                 if isinstance(a, dict) and (a.get("verb") or "") in _WRITERS]
        need: "_Counter" = _Counter((a.get("object") or "") for a in _acts)
        have: "_Counter" = _Counter()
        for s in framework.steps:
            nc = _ng.parse_name(s.tool or "") if (s.tool or "") != "final_answer" else None
            if nc and nc.verb in _WRITERS:
                have[nc.obj] += 1
        for wv in list(_WRITERS & dropped):
            objs = {(a.get("object") or "") for a in _acts if (a.get("verb") or "") == wv}
            if objs and all(have[o] >= need[o] for o in objs):
                dropped.discard(wv)
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
            # Routabile ANCHE via provider-suffix (ADR 0136): find_issues
            # liscio non esiste ma find_issues_github sì — derive_tool_name
            # non compone i qualifier provider e il flip avvelenava la
            # clausola sorgente a (find, entries) → find_entries sullo store
            # locale, GitHub mai interrogato (bug live task 35, 6/7:
            # ri-avvelenava la cache a ogni fire anche dopo la purga).
            _pfx = f"{v}_{o}"
            _routable_suffixed = any(
                n == _pfx or n.startswith(_pfx + "_") for n in names)
            if not _routable_suffixed and \
                    derive_tool_name(v, o, names) is None and \
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
        if not nc or nc.verb not in _PRODV or nc.obj in producer_set \
                or _fs_equivalent(nc.obj, producer_set):
            continue  # non-produttore o oggetto-intent legittimo/equivalente
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
    piano.

    LAYERING produttore-mancante (CP3, S2 ADR 0177 — NON ridondanza):
      1. `_enforce_missing_clauses` (VERB-level): copre i verbi RICHIESTI
         scoperti; usa `_align_foreign_producers_v3` come HELPER INTERNO per
         allineare un produttore estraneo già presente prima di appendere.
      2. `_enforce_missing_objects` (OBJECT-level, QUESTO): copre i drop
         PER-OGGETTO che il verb-level non vede.
    Complementari per costruzione (test `test_two_enforce_guards_compose_
    no_double_producer`): ogni oggetto ottiene ESATTAMENTE un produttore.

    Il guard verb-level NON vede i drop per-object: con N domini che condividono
    il verbo `find`, un `find_images` droppato resta nascosto (`find` risulta
    coperto da un altro dominio) → produttore-dominio perso silenziosamente
    (causa-radice del limite #domini).

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
            elif v in TRANSFORM_VERBS and o != "entries":
                # `entries` e' il carrier in-memory universale dei transformer,
                # non uno store esterno. Un extract/sort/filter su entries
                # consuma il produttore di dominio gia' presente; sintetizzare
                # `find_entries` richiederebbe uno `store` inesistente e tronca
                # la pipeline (turn live 845a773f). Gli intent che chiedono
                # davvero find/read entries restano coperti dal ramo PRODUCER.
                need.append(("find", o)); seen_need.add(o)
        new_steps: list = []
        added = set()
        for v, o in need:
            if o in produced or o in added:
                continue
            if _fs_equivalent(o, produced):
                # files↔dirs (§2.2 filesystem-siblings): list_dirs nel piano
                # COPRE l'oggetto-produttore `files` — senza, qui si appendeva
                # un find_files SPURIO dopo il piano corretto (hit id=246).
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


def _promote_count_cap(args: dict, schema: dict, clause: str) -> None:
    """Gemello di `_demote_overtight_caps` (E.2, 6/7): se la clausola CHIEDE
    una quantità N («3 foto», «primi 5») e lo step ha un arg-cap DICHIARATO
    ma il proposer NON l'ha valorizzato (assente) o l'ha messo più largo di N,
    inietta N. Così «mostrami 3 foto» produce davvero 3, non il default.
    Muta `args` sul posto. No-op se la clausola non chiede quantità, se il
    numero è una finestra temporale (escluso da _clause_requests_count), o se
    lo schema non dichiara un arg-cap. Deterministico §7.9."""
    if not isinstance(args, dict) or not isinstance(schema, dict):
        return
    if not _clause_requests_count(clause or ""):
        return
    try:
        from args_extractor import _extract_count
        n = _extract_count(clause or "")
    except Exception:
        n = None
    if not isinstance(n, int) or n <= 0:
        return
    props = schema.get("properties") if isinstance(
        schema.get("properties"), dict) else schema
    # inietta SOLO su un arg-cap DICHIARATO dallo schema (mai inventare args).
    declared = [nm for nm in _COUNT_CAP_ARGS
                if isinstance(props.get(nm), dict)]
    if not declared:
        return
    # preferenza deterministica stabile fra i dichiarati.
    for nm in ("max_results", "max_total", "top_k", "top", "limit"):
        if nm not in declared:
            continue
        cur = args.get(nm)
        if not isinstance(cur, int) or isinstance(cur, bool) or cur <= 0 or cur > n:
            args[nm] = n
        return


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
                    _promote_count_cap(st.args, schema, query)
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
                    _promote_count_cap(st.args, schema, query)
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
                # match esatto O equivalenza filesystem files↔dirs (§2.2):
                # list_dirs (obj=dirs) reclama il chunk «elenca i file della
                # cartella C:\…» (obj=files) — senza, il chunk col PATH andava
                # allo step create (stesso obj files) che se lo prendeva come
                # OUTPUT path (turn a9ec3b06).
                if not used[i] and co and (co == so
                                           or _fs_equivalent(so, [co])):
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
                _promote_count_cap(st.args, schema, chunk)
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
            # SINK (create/write): mai auto-riempire un path — sarebbe l'OUTPUT
            # path, e un path nella query è quasi sempre l'INPUT del produttore
            # (turn a9ec3b06: create.path=C:\Windows\… → file-mostro senza
            # estensione). L'output resta al default deliberato dell'executor
            # (§10.3 workspace) o alla scelta esplicita già nel piano.
            nc_w = _ng.parse_name(st.tool or "")
            if nc_w and nc_w.verb in ("create", "write"):
                for _pk in ("path", "base_path", "paths"):
                    extracted.pop(_pk, None)
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


_GW_CLIENT_TOOLS = frozenset({"find_files", "read_files", "get_files",
                              "list_dirs", "find_dirs"})
_GW_PHANTOM_PATHS = frozenset({"gdrive", "google drive", "googledrive",
                               "google_drive", "google-drive", "drive"})


def _drive_search_term(query: str) -> str:
    """Estrae un termine di ricerca «pulito» (nome-file) da una query Drive NL,
    togliendo verbo + frase-provider + filler documentali. Deterministico (§7.9),
    best-effort: il find Drive cerca per name-contains («il documento KAKEBO»→0,
    «KAKEBO SPESE 2026»→match)."""
    import re
    q = " " + (query or "") + " "
    q = re.sub(r"(?i)\b(su|sul|sullo|sulla|in|nel|nello|dentro|da|dal|dallo|from|on)\s+"
               r"(google\s*drive|g\s*drive|gdrive|google\s*docs?|google\s*sheets?|"
               r"google\s*fogli|drive|google)\b", " ", q)
    q = re.sub(r"(?i)\b(cerca(mi)?|trova(mi)?|search|find|apri|open|leggi|read|"
               r"scarica|download|mostra(mi)?|show)\b", " ", q)
    q = re.sub(r"(?i)\b(il|lo|la|i|gli|le|un|uno|una|the|a|an|di|del|della|dei|degli)\b", " ", q)
    q = re.sub(r"(?i)\b(documento|documenti|document|file|foglio|fogli|"
               r"spreadsheet|sheet|doc|cartella|folder)\b", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def _clause_scoped_drive_term(query: str, phantom: Optional[str] = None) -> str:
    """Termine di ricerca Drive dalla CLAUSOLA del produttore, non dalla query
    intera (§7.9, turn bb977a14). `_drive_search_term(query)` su un compound
    trascina la clausola-sink («KAKEBO SPESE 2026 e crea con i dati data
    descrizione importo») → termine sporco. Qui si applica al CHUNK giusto: quello
    che contiene il marker-provider fantasma («google drive») o, in mancanza, la
    prima clausola-produttrice (find/read/get). Best-effort → whole-query."""
    try:
        from compound_decomposer import split_query_chunks, detect_chunk_action
        chunks = split_query_chunks(query)
        if phantom:
            for ch in chunks:
                if phantom in ch.lower():
                    t = _drive_search_term(ch)
                    if t:
                        return t
        for ch in chunks:
            act = detect_chunk_action(ch)
            if act and act[0] in ("find", "read", "get"):
                t = _drive_search_term(ch)
                if t:
                    return t
    except Exception:  # noqa: BLE001
        pass
    return _drive_search_term(query)


_UNIVERSAL_GLOBS = frozenset({"*", "*.*", "**"})

# Arg path-ish di SCOPE che il proposer/una cache possono avvelenare con un
# path dell'install root (pattern-by-example / default appreso / piano cachato).
_PATHISH_SCOPE_ARGS = ("base_path", "path")


def _overwrite_phantom_install_args(framework: Framework,
                                    query: str) -> Framework:
    """§7.3 (turni 2cd8862a/e130c549): un arg path (`base_path`/`path`) che
    punta DENTRO l'install root di Metnos (`/opt/metnos/executors/…`) e che la
    query NON nomina è un FANTASMA — entra dal pattern-by-example del proposer,
    da un default appreso avvelenato o da un piano L0 cachato rotto. Qui viene
    RIMOSSO (i guard a valle — `_fill_clause_args` — lo ri-derivano dalla
    clausola; senza, l'executor chiede/erra ONESTO). Gira anche sugli hit
    cache (ADR 0174): ripara i piani già avvelenati. Se la query nomina
    esplicitamente l'install root («conta le LOC di /opt/metnos/runtime») il
    valore è legittimo e resta."""
    try:
        from args_resolver import _is_install_root_path
        q = (query or "")
        for s in framework.steps:
            a = getattr(s, "args", None) or {}
            for k in _PATHISH_SCOPE_ARGS:
                v = a.get(k)
                if isinstance(v, str) and _is_install_root_path(v) \
                        and v.strip() not in q:
                    log.info("[phantom_install] %s.%s=%r rimosso "
                             "(install-root non nominato dalla query)",
                             s.tool, k, v)
                    a.pop(k, None)
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("overwrite_phantom_install_args noop (best-effort): %r", ex)
        return framework


def _dl_match(concept: str, text: str) -> bool:
    """Wrapper best-effort su detection_lexicon.match (§7.3): NL→canonico dal
    lessico seedato IT+EN, mai liste-sinonimi hardcoded. Import lazy (il modulo
    apre il DB lessico); False su qualunque errore = fail-safe (nessun rewrite)."""
    try:
        import detection_lexicon as _dl
        return bool(_dl.match(concept, text or ""))
    except Exception:  # noqa: BLE001
        return False


def _route_text_web_image_search(framework: Framework, intent, query: str,
                                 _catalog=None) -> Framework:
    """Separa deterministicamente text-search e reverse image search.

    Una richiesta image-only che nomina il web ma non una sorgente/similarita'
    e' ``testo -> web``. Un piano che la trasforma in ``corpus locale ->
    Vision`` cambia scope e puo' inviare file privati a un provider remoto.
    In tal caso collassa i produttori locali e la ricerca inversa in un solo
    ``find_images_web(queries=[query])``. Le superfici linguistiche arrivano
    esclusivamente dal detection lexicon.
    """
    if not _dl_match("images.web_search_scope", query) \
            or _dl_match("images.reverse_search_intent", query):
        return framework
    objects = {
        str(getattr(intent, "object", "") or "").lower(),
        *{
            str(action.get("object") or "").lower()
            for action in (getattr(intent, "actions", None) or [])
            if isinstance(action, dict)
        },
    }
    objects.discard("")
    if objects and objects != {"images"}:
        return framework

    steps = list(getattr(framework, "steps", []) or [])
    source_tools = {
        "find_images_indices", "find_persons_indices", "read_persons",
        "find_files",
    }
    relevant = [
        pos for pos, step in enumerate(steps, start=1)
        if (getattr(step, "tool", "") or "") in source_tools
        or (getattr(step, "tool", "") or "") == "find_images_web"
    ]
    if not relevant:
        return framework
    web_steps = [
        step for step in steps
        if (getattr(step, "tool", "") or "") == "find_images_web"
    ]
    if len(relevant) == 1 and web_steps:
        args = dict(getattr(web_steps[0], "args", {}) or {})
        if args.get("queries") and not any(
                args.get(key) for key in ("paths", "urls", "from_step")):
            return framework

    insertion = min(relevant)
    inherited_max = next((
        (getattr(step, "args", {}) or {}).get("max_results")
        for step in web_steps
        if (getattr(step, "args", {}) or {}).get("max_results") is not None
    ), None)
    idx_map: dict[int, int] = {}
    new_steps: list[StepSpec] = []
    inserted_pos = 0
    for old_pos, step in enumerate(steps, start=1):
        tool = (getattr(step, "tool", "") or "")
        if old_pos == insertion:
            prior_args = dict(getattr(step, "args", {}) or {})
            direct_args = {"queries": [query]}
            max_results = prior_args.get("max_results", inherited_max)
            if max_results is not None:
                direct_args["max_results"] = max_results
            new_steps.append(StepSpec(tool="find_images_web", args=direct_args))
            inserted_pos = len(new_steps)
        if tool in source_tools or tool == "find_images_web":
            idx_map[old_pos] = inserted_pos
            continue
        new_steps.append(StepSpec(
            tool=tool,
            args=dict(getattr(step, "args", {}) or {}),
            if_prev_entries_nonempty=step.if_prev_entries_nonempty,
        ))
        idx_map[old_pos] = len(new_steps)

    for step in new_steps:
        step.args = _remap_step_refs(step.args, idx_map)
    log.info("[image_web_mode] ricerca testuale diretta; rimossi %d step locali",
             len(relevant) - 1)
    return Framework(
        steps=new_steps,
        fillers=getattr(framework, "fillers", {}) or {},
        final_message=_remap_step_refs(
            getattr(framework, "final_message", ""), idx_map),
    )


# Campi-DIMENSIONE che un compute somma per pesare (find_files espone `size`;
# `total_bytes` è il campo per-directory di find_dirs — il proposer lo cita a
# volte anche sul ramo file). Chiuso: NON include `size_min/size_max/file_count`
# (aggregati per-dir, non «peso della cartella»).
_SIZE_SUM_KEYS = frozenset({"size", "total_bytes", "size_bytes", "bytes",
                            "filesize", "file_size"})
# Tool che enumerano un CONTENITORE-cartella (chiave del path per ciascuno).
_FS_CONTAINER_PRODUCERS = {"find_dirs": "base_path", "find_files": "base_path",
                           "list_dirs": "path"}


def _route_folder_size(framework: Framework, query: str,
                       catalog: Optional[list]) -> Framework:
    """§7.9 (follow-up size-misroute, turn 5cdf80d0): «quanto è grande la
    cartella X» = intento DIMENSIONE-cartella. Il peso di una cartella = somma
    del PESO DEI FILE ricorsivi → il piano corretto è
    `find_files(recursive)` → `compute_entries(op=sum, key=size)`. Il proposer
    sbaglia in due modi, entrambi qui riparati:

    (A) STRUTTURALE (turn 5cdf80d0, ramo PC): compone
        `find_dirs(recursive)`→`compute_entries(op=sum,key=size)`. Ma find_dirs
        enumera le SOTTOCARTELLE (entries senza campo `size`, total_bytes=0 sul
        ramo remoto) → somma null/0, risposta vuota (errore ONESTO §2.8 ma
        errore). Fix: find_dirs→find_files(recursive), key→`size`. Trigger PURO-
        STRUTTURALE (nessuna parola-query): compute SOMMA/media di un campo-
        dimensione che consuma (from_step) un find_dirs.

    (B) INTENTO (ramo locale): compone `find_dirs(recursive)` DA SOLO e la
        risposta conta le sottodir («…contengono 16 directory») — mai pesa i
        file. Trigger: la query matcha il concept `fs.size_query`
        (detection_lexicon, IT+EN, §7.3 no-hardcoding) E il piano ha UN
        produttore-contenitore TERMINALE (nessuno step lo consuma) senza compute
        a valle → riscrive quel produttore in
        `find_files(base_path=X, recursive=true)` e inserisce
        `compute_entries(from_step, op=sum, key=size)`.

    Idempotente (find_files+compute non ri-scatta). No-op se il catalogo non ha
    find_files/compute_entries. Best-effort."""
    try:
        steps = list(getattr(framework, "steps", None) or [])
        if not steps:
            return framework
        names = catalog_names(catalog)
        if "find_files" not in names:
            return framework

        def _args(s):
            a = getattr(s, "args", None)
            return a if isinstance(a, dict) else {}

        changed = False

        # ── (A) strutturale: compute(sum,size) ← find_dirs ────────────────
        for ci, consumer in enumerate(steps):
            if (getattr(consumer, "tool", "") or "") != "compute_entries":
                continue
            a = _args(consumer)
            op = str(a.get("op") or "").strip().lower()
            key = str(a.get("key") or "").strip().lower()
            if op not in ("sum", "avg", "mean") or key not in _SIZE_SUM_KEYS:
                continue
            # Produttore consumato: from_step esplicito OPPURE — quando assente —
            # lo step IMMEDIATAMENTE precedente (l'engine concatena le entries
            # implicitamente; il planner spesso omette from_step, turn 5cdf80d0).
            fs = a.get("from_step")
            if isinstance(fs, int) and 1 <= fs <= len(steps):
                prod_idx = fs
            elif ci >= 1:
                prod_idx = ci        # 1-based dello step precedente
            else:
                continue
            prod = steps[prod_idx - 1]
            if (getattr(prod, "tool", "") or "") != "find_dirs":
                continue
            prod.tool = "find_files"
            pa = _args(prod)
            prod.args = pa
            pa["recursive"] = True     # peso cartella = file RICORSIVI
            if key != "size":          # find_files espone `size`, non total_bytes
                consumer.args["key"] = "size"
            # Re-pipe LIVE dal produttore riscritto: una cache L0 può aver
            # bake-ato le entries CONCRETE del vecchio find_dirs in compute.entries
            # (30 sottodir senza `size`) → senza questo, compute somma le stantie e
            # torna null (bug turn 5cdf80d0 sul PC). Scarta le entries bake-ate e
            # rimetti il from_step al produttore.
            consumer.args["from_step"] = prod_idx
            consumer.args.pop("entries", None)
            changed = True

        # ── (B) intento: produttore-contenitore terminale senza compute ───
        if "compute_entries" in names and query \
                and _dl_match("fs.size_query", query):
            # Uno step è "consumato" se qualcuno lo referenzia via from_step.
            consumed_idx = {_args(s).get("from_step") for s in steps
                            if isinstance(_args(s).get("from_step"), int)}
            has_size_compute = any(
                (getattr(s, "tool", "") or "") == "compute_entries"
                and str(_args(s).get("key") or "").lower() in _SIZE_SUM_KEYS
                for s in steps)
            if not has_size_compute:
                for idx, prod in enumerate(steps, start=1):
                    tool = getattr(prod, "tool", "") or ""
                    pathkey = _FS_CONTAINER_PRODUCERS.get(tool)
                    if not pathkey or idx in consumed_idx:
                        continue
                    a = _args(prod)
                    base = a.get(pathkey) or a.get("base_path") or a.get("path")
                    if not (isinstance(base, str) and base.strip()):
                        continue
                    # normalizza a find_files(base_path=X, recursive)
                    prod.tool = "find_files"
                    new_pa = {"base_path": base, "recursive": True}
                    for k, v in a.items():         # preserva runtime/selettori
                        if str(k).startswith("_") or k in (
                                "client", "pattern", "patterns", "query",
                                "name", "max_results"):
                            new_pa[k] = v
                    prod.args = new_pa
                    # inserisce compute(sum,size) subito dopo il produttore
                    from .types import StepSpec
                    comp = StepSpec(tool="compute_entries",
                                    args={"from_step": idx, "op": "sum",
                                          "key": "size"})
                    # renumber: ogni from_step >= idx+1 slitta di 1 (inseriamo
                    # a posizione idx+1). L'unico consumer possibile è a valle.
                    for s in steps:
                        sfs = _args(s).get("from_step")
                        if isinstance(sfs, int) and sfs >= idx + 1:
                            s.args["from_step"] = sfs + 1
                    steps.insert(idx, comp)
                    framework.steps = steps
                    changed = True
                    break   # una cartella-target per turno (caso reale)

        if changed:
            # NB: il template final_message del proposer può essere STANTÌO
            # rispetto al piano riscritto («…contengono N directory»), ma il
            # finalizer presenta AUTORITATIVAMENTE la riduzione scalare
            # (compute sum-size) prima del render — non serve toccarlo qui.
            log.info("[folder_size §7.9] piano→find_files(recursive)+compute("
                     "sum,size): peso cartella = file ricorsivi")
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("route_folder_size noop (best-effort): %r", ex)
        return framework


def _degenerate_find_to_list(framework: Framework, intent,
                             catalog: Optional[list]) -> Framework:
    """§2.2 (turn 8b675402): con intento LIST, un `find_files(base_path=X)`
    SENZA selettore discriminante (pattern/query/name assenti o glob universale)
    è un'ENUMERAZIONE DI CONTENITORE — la semantica di `list` («list=container
    enum», §2.2) — non una ricerca: swap deterministico a `list_dirs(path=X)`.

    Perché conta: «elenca i file della cartella C:\\… sul PC» → l'intent è
    (list, files) e il proposer sceglie find_files; ma find_files NON è
    device-eligible (C7 non ancora) → esecuzione LOCALE su un path Windows →
    errore, mentre list_dirs gira sul device. Il fix è semantico, non di
    placement: find-senza-selettore = list.

    Conservativo: solo verb `list` nell'intent; solo client locale (un find
    Drive è un listing gw legittimo); mai con from_step (consuma entries),
    count_only o selettore reale; tool-existence-safe. `recursive` esplicito
    preservato (default list_dirs = non-ricorsivo, la lettura onesta di
    «elenca»); passano solo gli arg dello schema list_dirs."""
    try:
        verbs = {(a.get("verb") or "") for a in (getattr(intent, "actions", None) or [])
                 if isinstance(a, dict)}
        verbs.add(getattr(intent, "verb", "") or "")
        if "list" not in verbs:
            return framework
        names = catalog_names(catalog)
        if "list_dirs" not in names:
            return framework
        for s in framework.steps:
            if (s.tool or "") != "find_files":
                continue
            a = s.args or {}
            if a.get("client") not in (None, "", "local"):
                continue
            if isinstance(a.get("from_step"), int) or a.get("count_only"):
                continue
            base = a.get("base_path")
            if not (isinstance(base, str) and base.strip()):
                continue
            selectors = []
            for k in ("pattern", "query", "name"):
                v = a.get(k)
                if isinstance(v, str) and v.strip():
                    selectors.append(v.strip())
            pats = a.get("patterns")
            if isinstance(pats, list):
                selectors.extend(str(p).strip() for p in pats if str(p).strip())
            if any(sel not in _UNIVERSAL_GLOBS for sel in selectors):
                continue                      # selettore reale → resta find
            new_args = {"path": base}
            for k in ("recursive", "sort", "max_results", "max_depth"):
                if k in a:
                    new_args[k] = a[k]
            for k, v in a.items():            # runtime keys (_actor, _lang, …)
                if isinstance(k, str) and k.startswith("_"):
                    new_args[k] = v
            log.info("[degenerate_find §2.2] find_files(base_path=%r, no "
                     "selettore) -> list_dirs (intent list)", base)
            s.tool = "list_dirs"
            s.args = new_args
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("degenerate_find_to_list noop (best-effort): %r", ex)
        return framework


_SINK_VERBS = frozenset({"create", "write", "set", "order"})

# Cap RECORD per un extract che alimenta un SINK BULK (create/write foglio/csv):
# il default extract_entries (20) è per estrazioni piccole (eventi da 1 mail) e
# TRONCAVA in silenzio un foglio da 77 righe a 20 (turn 8167889d, §2.8). Il budget
# token (8192) resta il limite reale (~80-100 record/chiamata) e flagga la
# troncatura oltre; per sorgenti enormi serve il chunking (follow-up).
_BULK_EXTRACT_CAP = 500
_SITES_COLLECTION_EXTRACT_CAP = 100


def _scope_sink_provider_to_clause(framework: Framework, query: str,
                                   catalog: Optional[list]) -> Framework:
    """§7.9 (turn bb977a14): il provider di un SINK (create/write files) e'
    CLAUSE-SCOPED, non whole-query. «cerca SU GOOGLE DRIVE X e crea un foglio»:
    il marker gw e' nella clausola-PRODUTTRICE; la clausola-create NON lo nomina
    → default LOCAL (§10.3), NON eredita gw dalla query intera (che creerebbe un
    foglio-doppione su Drive). Se il create nomina gw esplicito («salva SU DRIVE»)
    → gw. Imposta `client` esplicito sul sink risolvendo dal TESTO delle
    clausole-sink; `resolve_backend_arg` poi lo RISPETTA (non scavalca). Solo
    oggetti multi-provider, no-op se client gia' esplicito. Multi-clausola only."""
    try:
        import backend_resolver as _br
        from compound_decomposer import split_query_chunks
        from prefilter import tokenize as _tok, detect_canonical_verbs_all as _vb
        import naming_grammar as _ng
        chunks = split_query_chunks(query)
        if len(chunks) < 2:
            return framework
        sink_text = " ".join(
            ch.lower() for ch in chunks
            if ((_vb(_tok(ch)) or [None]) or [None])[0] in _SINK_VERBS)
        if not sink_text.strip():
            return framework
        for s in framework.steps:
            nc = _ng.parse_name(s.tool or "")
            if not nc or nc.verb not in _SINK_VERBS:
                continue
            spec = _br.OBJECT_BACKENDS.get(nc.obj)
            if not spec or len(spec.get("providers", [])) < 2:
                continue
            arg = spec["arg"]
            if isinstance(s.args.get(arg), str) and s.args.get(arg):
                continue                       # gia' esplicito → non toccare
            prov = _br.resolve(nc.obj, sink_text)   # CLAUSE-scoped (sink only)
            if prov:
                s.args[arg] = prov
                log.info("[sink_provider] %s client=%s (clause-scoped)",
                         s.tool, prov)
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("scope_sink_provider noop (best-effort): %r", ex)
        return framework


def _decontaminate_reader_qualifier(framework: Framework, query: str,
                                    catalog: Optional[list]) -> Framework:
    """§7.9 (turn bb977a14): un `read_<obj>_<fmt>` il cui qualifier-FORMATO non e'
    nominato dalla SUA clausola-produttrice ma SOLO da una clausola-SINK a valle
    («cerca il FILE X ... e crea uno SPREADSHEET ...») e' CONTAMINATO: il proposer
    ha copiato il formato d'USCITA sulla lettura. Demote a `read_<obj>` generico
    → il read risolve la FONTE reale (un Doc) per nome, invece di forzare il MIME
    del formato-uscita (che escluderebbe la fonte → ambiguita'/not_found).

    Clause-scoped: il formato e' legittimo se compare in una clausola-produttrice
    (find/read/get/list) — «leggi il FOGLIO X» tiene read_files_spreadsheet. Scatta
    solo se il formato compare NELLA/E clausola-sink e in NESSUNA produttrice.
    Tool-existence-safe (demote solo se `read_<obj>` esiste). No-op su mono-clausola."""
    try:
        from compound_decomposer import split_query_chunks, _FORMAT_HINTS
        from prefilter import tokenize as _tok, detect_canonical_verbs_all as _verbs
        import naming_grammar as _ng
        chunks = split_query_chunks(query)
        if len(chunks) < 2:
            return framework
        names = catalog_names(catalog)
        names.discard(None)
        # reverse: qualifier-formato → parole-hint che lo nominano (IT+EN).
        fmt_hints: dict = {}
        for hint, (_o, qual) in _FORMAT_HINTS.items():
            fmt_hints.setdefault(qual, set()).add(hint)
        # Classifica ogni chunk per VERBO (non serve l'oggetto: detect_chunk_action
        # torna None se l'oggetto non e' riconosciuto — «crea uno spreadsheet» ha
        # verbo create ma oggetto non lessicalizzato → cadrebbe in prod). Sink =
        # chunk con verbo create/write; produttore = tutto il resto.
        prod_blob, sink_blob = [], []
        for ch in chunks:
            vs = _verbs(_tok(ch))
            v = vs[0] if vs else None
            (sink_blob if v in ("create", "write") else prod_blob).append(ch.lower())
        prod_blob = " ".join(prod_blob)
        sink_blob = " ".join(sink_blob)
        for s in framework.steps:
            nc = _ng.parse_name(s.tool or "")
            if not nc or nc.verb != "read" or not nc.qualifier:
                continue
            hints = fmt_hints.get(nc.qualifier)
            if not hints:                       # qualifier non-formato (_ocr…) → skip
                continue
            generic = f"read_{nc.obj}"
            if generic not in names:
                continue
            in_prod = any(h in prod_blob for h in hints)
            in_sink = any(h in sink_blob for h in hints)
            if (not in_prod) and in_sink:
                log.info("[reader_decontam] %s -> %s (formato %r solo nel sink)",
                         s.tool, generic, nc.qualifier)
                s.tool = generic
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("decontaminate_reader_qualifier noop (best-effort): %r", ex)
        return framework


def _align_provider_client(framework: Framework, query: str,
                           catalog: Optional[list]) -> Framework:
    """§7.9 deterministico — provider Google via CLIENT-ARG (backend Drive), NON
    executor-suffisso (approccio ritirato 4/7/2026). Se la query cita Google
    Drive/Docs (marker `google_workspace`) e uno step usa un file-executor
    client-capable: imposta `client="google_workspace"`, ripulisce un path locale
    FANTASMA (`/gdrive`, che il proposer inventa da «google drive») e, se a
    `find_files` manca il termine di ricerca, lo deriva dalla query. Idempotente,
    best-effort, non blocca il turno. Bug: «cerca su google drive X» →
    find_files(local, base_path=/gdrive) → «percorso non trovato»."""
    try:
        from tool_grammar import active_provider_suffixes
        _sfx = active_provider_suffixes(query) or []
        if not any((x or "").lstrip("_") == "google_workspace" for x in _sfx):
            return framework
    except Exception:  # noqa: BLE001
        return framework
    try:
        touched = False
        for s in framework.steps:
            if (s.tool or "") not in _GW_CLIENT_TOOLS:
                continue
            if s.args.get("client") != "google_workspace":
                s.args["client"] = "google_workspace"
            for pk in ("base_path", "path", "paths"):
                v = s.args.get(pk)
                if isinstance(v, str) and v.strip().lower().strip("/") in _GW_PHANTOM_PATHS:
                    s.args.pop(pk, None)
                elif isinstance(v, list):
                    v2 = [x for x in v if str(x).strip().lower().strip("/") not in _GW_PHANTOM_PATHS]
                    s.args[pk] = v2 if v2 else None
                    if not v2:
                        s.args.pop(pk, None)
            if s.tool == "find_files":
                # FIX-2: il termine di ricerca del proposer e' spesso il MARKER
                # PROVIDER stesso («google drive») invece del nome-file → find
                # torna spazzatura. OVERWRITE clause-scoped quando il valore e'
                # un fantasma-provider (o assente); i valori legittimi restano.
                _cur = None
                _cur_key = None
                for _k in ("query", "pattern", "patterns", "paths"):
                    _v = s.args.get(_k)
                    if isinstance(_v, list):
                        _v = _v[0] if _v else None
                    if isinstance(_v, str) and _v.strip():
                        _cur, _cur_key = _v.strip(), _k
                        break
                _phantom = (_cur or "").lower().strip("/") in _GW_PHANTOM_PATHS
                if _cur is None or _phantom:
                    _ph = (_cur or "").lower() if _phantom else "google drive"
                    _t = _clause_scoped_drive_term(query, _ph)
                    if _t and _t.lower() != (_cur or "").lower():
                        for _k in ("query", "pattern", "patterns", "paths"):
                            s.args.pop(_k, None)
                        s.args["query"] = _t
                        log.info("[provider_client] find_files term OVERWRITE "
                                 "%r → %r (clause-scoped)", _cur, _t)
            touched = True
        if touched:
            log.info("[provider_client] google_workspace → client su file-executor (Drive)")
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("align_provider_client noop (best-effort): %r", ex)
        return framework


# ── CONTRATTO D'ORDINE della pipeline guard (ADR 0177 T3, CP1·M0) ──────────
# L'ORDINE è il contratto: ogni entry = (nome, v3_only, fn(fw,intent,query,cat)).
# `test_guard_pipeline_contract.py` blocca nomi+ordine: chi inserisce/sposta un
# guard DEVE aggiornare il test consapevolmente. Il razionale di ogni guard vive
# nel SUO docstring; qui solo il vincolo di posizione quando esiste:
#   - phantom_install PRIMA di tutto (i guard a valle ri-derivano dalla clausola).
#   - decontaminate_reader PRIMA di ensure_extract (il read demolito a generico
#     è il content-reader che fa scattare il trigger strutturale, bb977a14).
#   - fill_clause_args DOPO conform (opera su step già in ordine-intent);
#     resolve_store_field_refs DOPO fill (args ormai stabili).
#   - route_mail/filename/provider DOPO i guard di struttura, sempre (no gate v3).
#   - scope_sink_provider DOPO align_provider_client (prima i produttori→gw,
#     poi i sink→clause-scoped local §10.3).
#   - degenerate_find ULTIMO (un find instradato a gw dal provider-guard non
#     va toccato; il fill gli ha già dato il base_path di clausola).
def _coerce_args_to_schema(framework: Framework,
                           catalog: Optional[list]) -> Framework:
    """FASE 3.1 provenienza (spec §3.1): backstop deterministico UNICO sul
    confine LLM→pipeline — conforma gli args di ogni step allo schema del suo
    tool. Gli arg che un guard a valle DICHIARA nei suoi `writes` (registro
    PROV.1) sono esenti: dominio dei guard, toccarli romperebbe l'idempotenza
    della catena. Implementazione (e razionale) in `engine/coerce_args.py`."""
    try:
        from engine.coerce_args import coerce_framework_to_schema
        owned = frozenset(
            w.split(".", 1)[1]
            for g in GUARD_PIPELINE
            for w in g.writes
            if w.startswith("args.") and not w.endswith(".*"))
        return coerce_framework_to_schema(framework, catalog,
                                          guard_owned_args=owned)
    except Exception as ex:  # noqa: BLE001 — backstop best-effort
        log.warning("coerce_args noop (best-effort): %r", ex)
        return framework


_IPV4_RE = re.compile(
    r"(?<![\w.])((?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?::(\d{1,5}))?(?![\w.])")


def _site_url_from_host_token(text: str) -> Optional[str]:
    """Deriva un URL sito da un IPv4 nudo (con porta opzionale) in `text`.

    I pannelli LAN/self-hosted (router, NAS) vengono nominati come IP senza
    schema; un IPv4 non ha TLD e sfugge alla regex dominio. Schema http://:
    è la norma degli admin panel locali e coincide col piano che l'utente ha
    già visto aprirsi. Ritorna None se non c'è un IPv4 valido."""
    if not text:
        return None
    m = _IPV4_RE.search(text)
    if not m:
        return None
    host = m.group(1)
    port = m.group(2)
    if port is not None and not (0 < int(port) <= 65535):
        return None
    return f"http://{host}:{port}" if port else f"http://{host}"


def _ensure_site_session_precursor(framework: Framework, intent, query: str,
                                   catalog: Optional[list]) -> Framework:
    """Spec sites F1/F2: un consumer (login/read/act_sites)
    ha bisogno di una SESSIONE, prodotta da `open_sites`. Il PLANNER locale
    (Qwen) tende a emettere il solo consumer (login_sites) senza il precursore
    e senza wiring — il consumer fallisce «session_ids mancante». Guard
    DETERMINISTICO (§7.9): se il piano contiene login/read_sites SENZA sessione
    e SENZA open_sites, ricostruisce la catena canonica F1 dall'URL nella query:
    open_sites → [login_sites] → [read_sites]. Idempotente (T4): se open_sites
    è già presente, no-op.

    Sanitizza anche il `domain` URL-shaped che il planner mette per errore su
    login_sites (il broker default = origine della sessione, verificata §3.2):
    un domain=<url completo> romperebbe il match ESATTO host (CRITICO-1).
    `delete_sites` (kill-switch, ids/all) è ESCLUSO."""
    steps = list(getattr(framework, "steps", []) or [])
    if not steps:
        return framework
    _site_consumers = {"login_sites", "read_sites", "act_sites"}
    tools_present = {(getattr(s, "tool", "") or "") for s in steps}
    consumers = [s for s in steps
                 if (getattr(s, "tool", "") or "") in _site_consumers]
    acts = getattr(intent, "actions", None) or []
    action_objects = {
        (x.get("object") or "").lower()
        for x in acts if isinstance(x, dict)
    }
    root_object = str(getattr(intent, "object", "") or "").lower()
    strong_login_intent = (
        _dl_match("sites.login_intent", query)
        or _dl_match("sites.session_entry_intent", query))
    structured_record_request = (
        _dl_match("sites.structured_record_request", query)
        or _dl_match("sites.collection_search_request", query)
        or (_dl_match("sites.search_action_verb", query)
            and _dl_match("sites.goal_scope_quantifier", query)))
    has_site_context = ("open_sites" in tools_present
                        or root_object == "sites"
                        or "sites" in action_objects)
    if not consumers and not (
            (strong_login_intent or structured_record_request)
            and has_site_context):
        return framework  # nessun consumer sites → non ci riguarda

    # Deriva l'URL della sessione: da un open_sites già presente, poi dalla
    # query (esplicito), infine da un arg URL-shaped di un consumer (il planner
    # a volte mette l'URL in `domain`).
    import re as _re
    url = None
    original_open = None
    for s in steps:
        if (getattr(s, "tool", "") or "") == "open_sites":
            original_open = s
            u = (getattr(s, "args", {}) or {}).get("urls")
            if isinstance(u, list) and u and isinstance(u[0], str):
                url = u[0]
                break
            if isinstance(u, str) and u:
                url = u
                break
    if not url:
        m = _re.search(r"https?://[^\s'\"<>]+", query or "")
        if m:
            url = m.group(0).rstrip(".,;)")
    if not url:
        # UX sites: l'utente nomina normalmente un dominio, non un URL.
        # Derivazione stretta e deterministica; niente correzione ortografica
        # silenziosa (cloudfare.com resta cloudfare.com).
        m = _re.search(
            r"(?<![@\w])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z]{2,63})(?![\w])", query or "", _re.IGNORECASE)
        if m:
            url = "https://" + m.group(1).lower()
    if not url:
        # Pannelli LAN/self-hosted: l'utente nomina un IP nudo (router, NAS).
        # Un IPv4 non ha TLD, quindi la regex dominio non lo prende; qui lo
        # deriva su http:// (schema tipico degli admin panel locali, coerente
        # col piano che l'utente ha già visto funzionare).
        url = _site_url_from_host_token(query or "")
    if not url:
        for s in consumers:
            for v in (getattr(s, "args", {}) or {}).values():
                # Il planner può mettere l'host in un arg scalare O come unico
                # elemento di `session_ids` (scambiato per un session_id): in
                # entrambi i casi ne deriviamo l'URL della sessione mancante.
                for token in ((v,) if isinstance(v, str)
                              else tuple(v) if isinstance(v, (list, tuple))
                              else ()):
                    if not isinstance(token, str):
                        continue
                    if token.startswith("http"):
                        url = token.rstrip(".,;)")
                        break
                    derived = _site_url_from_host_token(token)
                    if not derived and _re.fullmatch(
                            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                            r"[a-z]{2,63}", token, _re.IGNORECASE):
                        derived = "https://" + token.lower()
                    if derived:
                        url = derived
                        break
                if url:
                    break
            if url:
                break
    if not url:
        return framework  # nessun URL derivabile → fallimento onesto a valle

    verbs = {(x.get("verb") or "").lower() for x in acts if isinstance(x, dict)}
    root_verb = str(getattr(intent, "verb", "") or "").lower()
    want_login = (strong_login_intent or root_verb == "login"
                  or "login" in verbs or "login_sites" in tools_present)
    want_read = (root_verb in ("read", "describe")
                 or "read" in verbs or "describe" in verbs
                 or "read_sites" in tools_present
                 or structured_record_request)
    want_act = (root_verb == "act" or "act" in verbs
                or "act_sites" in tools_present)
    if not (want_login or want_read or want_act):
        return framework

    # Ricostruzione DETERMINISTICA della catena canonica F1/F2: il planner
    # locale emette spesso gli step giusti ma non li ordina/incatena. Le azioni
    # che chiedono soltanto di raggiungere il form sono assorbite da
    # login_sites: ripeterle come act_sites creerebbe un secondo consenso e
    # scavalcherebbe la sua procedura intelligente. Tutte le azioni successive
    # restano nel piano. Idempotente (T4).
    open_args = {k: v for k, v in
                 dict(getattr(original_open, "args", {}) or {}).items()
                 if k in ("urls", "allowlist", "session_label", "max_total")}
    existing_urls = open_args.get("urls")
    if not (isinstance(existing_urls, list) and existing_urls):
        open_args["urls"] = [url]
    new_steps = [StepSpec(tool="open_sites", args=open_args)]

    original_acts = [s for s in steps
                     if (getattr(s, "tool", "") or "") == "act_sites"]

    def _is_login_entry_action(step) -> bool:
        action = str((getattr(step, "args", {}) or {}).get("action") or "")
        if not action or not _dl_match("sites.login_entry_target", action):
            return False
        try:
            from playwright_sidecar.action_resolver import parse_action
            parsed = parse_action(action)
            return bool(parsed.get("ok")
                        and parsed.get("primitive") in ("click", "goto"))
        except Exception:  # noqa: BLE001 -- nessun rewrite se il resolver manca
            return False

    login_entry_acts = ([s for s in original_acts if _is_login_entry_action(s)]
                        if want_login else [])
    post_login_acts = ([s for s in original_acts if s not in login_entry_acts]
                       if want_login else original_acts)

    # Una ricerca nominata DOPO l'accesso appartiene alla sessione autenticata,
    # non al crawler pubblico find_urls. Conserva la frase naturale come fine di
    # act_sites; l'executor la scompone internamente in navigazioni bounded.
    search_chunks: list[str] = []
    absorbed_site_search = False
    if want_login and not _dl_match("sites.external_search_scope", query):
        try:
            from compound_decomposer import split_query_chunks
            search_chunks = [
                chunk.strip(" ,.;") for chunk in split_query_chunks(query or "")
                if _dl_match("sites.search_action_verb", chunk)
            ]
        except Exception:  # noqa: BLE001 -- nessuna inferenza se split fallisce
            search_chunks = []
        existing_actions = {
            str((getattr(step, "args", {}) or {}).get("action") or "").strip().lower()
            for step in post_login_acts
        }
        has_search_action = any(
            _dl_match("sites.search_action_verb", action)
            for action in existing_actions)
        try:
            import urllib.parse as _urlparse
            session_host = (_urlparse.urlsplit(url).hostname or "").lower()
        except ValueError:
            session_host = ""
        for chunk in search_chunks:
            domains = _re.findall(
                r"(?<![@\w])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                r"[a-z]{2,63})(?![\w])", chunk, _re.IGNORECASE)
            if any(domain.lower() != session_host for domain in domains):
                continue
            if has_search_action or chunk.lower() in existing_actions:
                continue
            post_login_acts.append(StepSpec(
                tool="act_sites", args={"action": chunk}))
            existing_actions.add(chunk.lower())
            has_search_action = True
        if search_chunks:
            want_read = True
            absorbed_site_search = True

    # Per leggere record da una sezione non basta aprire la sessione: prima
    # bisogna attestare di avere raggiunto il contenitore richiesto. Se il
    # planner non ha emesso una navigazione, recluta act_sites in modalita'
    # fine semantico. La query resta linguaggio naturale; la riduzione bounded
    # avviene dentro l'executor intelligente e il planner non vede nuovi tipi.
    if structured_record_request and not post_login_acts:
        post_login_acts.append(StepSpec(tool="act_sites", args={
            "action": query, "_goal_mode": True,
        }))
        want_act = True

    def _append_acts(source_steps) -> None:
        for original_act in source_steps:
            original_args = dict(getattr(original_act, "args", {}) or {})
            act_args = {"from_step": len(new_steps)}
            for key in ("action", "value_ref", "_goal_mode"):
                if original_args.get(key) is not None:
                    act_args[key] = original_args[key]
            new_steps.append(StepSpec(tool="act_sites", args=act_args))

    if want_login:
        original_login = next((s for s in steps
                               if (getattr(s, "tool", "") or "") ==
                               "login_sites"), None)
        login_args = {k: v for k, v in
                      dict(getattr(original_login, "args", {}) or {}).items()
                      if k in ("domain", "form_hint")}
        domain = login_args.get("domain")
        if isinstance(domain, str) and domain.startswith(("http://", "https://")):
            try:
                import urllib.parse as _urlparse
                login_args["domain"] = _urlparse.urlsplit(domain).hostname or ""
            except ValueError:
                login_args.pop("domain", None)
        login_args["from_step"] = len(new_steps)
        new_steps.append(StepSpec(tool="login_sites", args=login_args))

    # Dopo il login esegui le azioni richieste (ricerca, navigazione, ecc.) e
    # leggi infine lo stato risultante. La stessa sequenza e' necessaria per
    # una richiesta strutturata pubblica: extract deve ricevere il testo DOPO
    # l'azione. I turni pubblici non strutturati preservano il vecchio read→act.
    if want_login or structured_record_request:
        _append_acts(post_login_acts)
    if want_read:
        original_read = next((s for s in steps
                              if (getattr(s, "tool", "") or "") ==
                              "read_sites"), None)
        read_args = {k: v for k, v in
                     dict(getattr(original_read, "args", {}) or {}).items()
                     if k in ("include_screenshot", "include_forms")}
        # Il testo + URL sono il risultato predefinito. Lo screenshot resta
        # opt-in: oltre a essere ridondante con un link navigabile, un allegato
        # immagine a monte non deve oscurare un file prodotto a valle.
        read_args.setdefault("include_screenshot", False)
        read_args["from_step"] = len(new_steps)
        new_steps.append(StepSpec(tool="read_sites", args=read_args))
    if not want_login and not structured_record_request:
        _append_acts(post_login_acts)

    # Una richiesta enumerativa sulla pagina richiede un confine strutturato:
    # read_sites produce il blob testuale, extract_entries scopre internamente
    # il piccolo schema (nessun campo/domain nel router), describe_entries lo
    # presenta. `drill_down=False` confina l'estrazione alla pagina autenticata:
    # i link osservati non diventano fetch stateless fuori sessione.
    existing_extract = next((
        step for step in steps
        if (getattr(step, "tool", "") or "") == "extract_entries"
    ), None)
    original_describes = [
        (pos, step) for pos, step in enumerate(steps, start=1)
        if (getattr(step, "tool", "") or "") == "describe_entries"
    ]
    auto_extract = bool(
        structured_record_request and want_read and existing_extract is None)
    site_output_pos = len(new_steps)
    inferred_extract_pos = 0
    canonical_describe_pos = 0
    absorbed_describe_positions: set[int] = set()
    if auto_extract:
        inferred_extract_pos = len(new_steps) + 1
        new_steps.append(StepSpec(tool="extract_entries", args={
            "from_step": site_output_pos,
            "instruction": query,
            "drill_down": False,
            "max_per_text": _SITES_COLLECTION_EXTRACT_CAP,
            "max_total": _SITES_COLLECTION_EXTRACT_CAP,
        }))
        original_describe = original_describes[0][1] \
            if original_describes else None
        describe_args = dict(
            getattr(original_describe, "args", {}) or {})
        describe_args.pop("entries", None)
        describe_args["from_step"] = inferred_extract_pos
        # Una richiesta di collezione vuole i record, non una sintesi lossy.
        # `compact` su data_kind=entries usa il renderer deterministico di
        # describe_entries: una riga per record e tutti i campi non vuoti.
        describe_args["style"] = "compact"
        describe_args.setdefault("context", query)
        describe_args["data_kind"] = "entries"
        canonical_describe_pos = len(new_steps) + 1
        new_steps.append(StepSpec(
            tool="describe_entries", args=describe_args,
            # Deve produrre anche la risposta onesta a zero record; saltarlo
            # lascerebbe il final_message senza uno step risolvibile.
            if_prev_entries_nonempty=False,
        ))
        absorbed_describe_positions = {
            pos for pos, _step in original_describes}

    # La catena Sites e' un PRODUTTORE, non un terminale obbligatorio. Conserva
    # trasformazioni e sink successivi (extract, spreadsheet, send, ...),
    # rimappandoli sul `read_sites` canonico. Prima questa ricostruzione li
    # scartava tutti: una richiesta «leggi dal sito e crea un foglio» terminava
    # quindi con la sola sintesi della pagina.
    canonical_pos = len(new_steps)
    site_tools = {"open_sites", "login_sites", "act_sites", "read_sites"}
    absorbed_web_tools = ({"find_urls", "read_urls_html", "read_urls_pdf",
                           "get_urls"} if absorbed_site_search else set())
    preserved: list[tuple[int, object]] = []
    finals: list[tuple[int, object]] = []
    idx_map: dict[int, int] = {}
    for old_pos, step in enumerate(steps, start=1):
        tool = (getattr(step, "tool", "") or "")
        if tool == "final_answer":
            finals.append((old_pos, step))
        elif old_pos in absorbed_describe_positions:
            idx_map[old_pos] = canonical_describe_pos
        elif tool in site_tools or tool in absorbed_web_tools:
            # Nell'inserzione automatica i consumer a valle devono vedere i
            # record, non il blob di read_sites. L'extract stesso e' gia' stato
            # costruito manualmente sul producer raw e non passa da idx_map.
            idx_map[old_pos] = (inferred_extract_pos
                                if auto_extract else site_output_pos)
        else:
            preserved.append((old_pos, step))
    for offset, (old_pos, _step) in enumerate(preserved, start=1):
        idx_map[old_pos] = canonical_pos + offset
    final_pos = canonical_pos + len(preserved) + 1
    for old_pos, _step in finals:
        idx_map[old_pos] = final_pos

    for _old_pos, step in preserved:
        new_steps.append(StepSpec(
            tool=step.tool,
            args=_remap_step_refs(dict(getattr(step, "args", {}) or {}), idx_map),
            if_prev_entries_nonempty=step.if_prev_entries_nonempty,
        ))
    if finals:
        step = finals[0][1]
        new_steps.append(StepSpec(
            tool=step.tool,
            args=_remap_step_refs(dict(getattr(step, "args", {}) or {}), idx_map),
            if_prev_entries_nonempty=step.if_prev_entries_nonempty,
        ))
    final_message = _remap_step_refs(
        getattr(framework, "final_message", ""), idx_map)
    if auto_extract:
        # Funziona anche con output_policy disabilitata: la risposta terminale
        # e' sempre la presentazione dei record appena estratti.
        final_message = f"${{step{canonical_describe_pos}.summary}}"
    return Framework(
        steps=new_steps,
        fillers=getattr(framework, "fillers", {}) or {},
        final_message=final_message,
    )


def _normalize_document_report_pipeline(framework: Framework, intent,
                                        query: str,
                                        catalog: Optional[list]) -> Framework:
    """Normalizza un workflow documentale compound in una sola dataflow.

    Trigger strutturale stretto: sorgente file + estrazione + sink tabellare
    con colonne dichiarate + archivio. In questa classe i piani LLM lunghi
    tendevano a ramificare su producer indipendenti e a perdere il carrier
    ``entries``. La forma canonica mantiene una sola provenienza e applica
    create-only a ogni mutazione. Nessun delete/move e quindi nessun consenso
    preventivo e' necessario; una collisione fallisce chiusa.
    """
    try:
        from compound_decomposer import derive_extract_fields, derive_sink_fields
        observed_tools = {
            (getattr(step, "tool", "") or "")
            for step in (getattr(framework, "steps", None) or [])
        }
        # This normalizer owns document-only workflows.  Absorbing a mixed
        # source plan here would silently discard mail/calendar/contact
        # producers before the multi-source normalizer can reconcile them.
        if observed_tools & {"read_messages", "read_events", "find_contacts"}:
            return framework
        actions = [a for a in (getattr(intent, "actions", None) or [])
                   if isinstance(a, dict)]
        verbs = {(a.get("verb") or "").lower() for a in actions}
        objects = {(a.get("object") or "").lower() for a in actions}
        sink_fields = derive_sink_fields(query)
        # Chat/UI clients may preserve pasted text as a Markdown blockquote.
        # Quote markers are presentation syntax, not semantic tokens: remove
        # only line-leading markers and collapse whitespace before closed
        # clause detection.  Keep the original query for report context.
        semantic_query = re.sub(r"(?m)^\s*>\s?", "", query or "")
        semantic_query = re.sub(r"\s+", " ", semantic_query).strip()
        ql = semantic_query.casefold()
        logical_dedup = bool(
            re.search(r"\bdeduplic\w*\b", ql)
            or (re.search(r"\bduplicat\w*\b", ql)
                and (re.search(r"\blogic\w*\b", ql)
                     or re.search(r"\bsenza\s+cancell\w*\b", ql)
                     or re.search(r"\bwithout\s+delet\w*\b", ql))))
        if not ({"extract", "compress"} <= verbs
                and verbs & {"create", "write"}
                and "files" in objects
                and len(sink_fields) >= 2
                and "move" not in verbs
                and ("delete" not in verbs or logical_dedup)):
            return framework
        names = catalog_names(catalog)
        required = {
            "find_files", "filter_entries", "read_files", "sort_entries",
            "describe_entries", "create_dirs", "write_files",
            "create_files_spreadsheet", "compress_files",
        }
        if not required <= names:
            return framework
        steps = list(getattr(framework, "steps", None) or [])
        finder = next((step for step in steps
                       if (step.tool or "") == "find_files"
                       and (step.args or {}).get("base_path")), None)
        if finder is None:
            return framework

        # Sorgente: conserva solo selettori filesystem/provider reali del
        # primo finder, mai entries/materializzazioni di un ramo parallelo.
        allowed_find = {
            "base_path", "pattern", "patterns", "recursive", "max_results",
            "max_depth", "case_sensitive", "client",
        }
        find_args = {key: value for key, value in dict(finder.args or {}).items()
                     if key in allowed_find}
        find_args.setdefault("recursive", True)

        old_filter = next((step for step in steps
                           if (step.tool or "") == "filter_entries"), None)
        filter_args = dict(getattr(old_filter, "args", None) or {})
        filter_args.pop("entries", None)
        # ``find_files`` espone due tassonomie distinte: ``type`` descrive
        # l'oggetto filesystem (file/dir/symlink), mentre ``kind`` descrive il
        # contenuto (document/text/binary/...).  Un proposer puo' confonderle
        # e produrre kind="file": il filtro e' sintatticamente valido ma
        # elimina ogni PDF/DOCX/XLSX.  In questo workflow il finder ha gia'
        # ristretto le estensioni; l'unico vincolo strutturale utile e' quindi
        # type="file".
        if filter_args.get("kind") == "file":
            filter_args.pop("kind", None)
            filter_args["type"] = "file"
        # I risultati sono volutamente creati sotto la cartella sorgente. Un
        # secondo run non deve reingerire il proprio XLSX (o quelli dei run
        # precedenti), altrimenti il report cresce ricorsivamente e il dedup
        # misura anche artefatti Metnos. Il filtro temporale resta sui campi
        # dedicati mtime_*; usiamo il predicato generico solo quando era vuoto
        # o conteneva il placeholder temporale ormai sostituito dal resolver.
        temporal_aliases = {
            "modified_time", "modification_time", "mtime", "modified",
            "last_modified", "data_modifica", "ultima_modifica",
        }
        where_field = str(filter_args.get("where_field") or "").casefold()
        if not where_field or where_field in temporal_aliases:
            for key in (
                    "where_value", "where_in", "where_not_in",
                    "where_starts_with", "where_contains", "where_glob"):
                filter_args.pop(key, None)
            filter_args.update({
                "where_field": "path",
                "where_regex": (
                    r"^(?!.*[\\/]Risultati_Metnos_[^\\/]+(?:[\\/]|$)).*$"
                ),
            })
        filter_args["from_step"] = 1

        old_reader = next((step for step in steps
                           if (step.tool or "") == "read_files"), None)
        read_args = {key: value for key, value in
                     dict(getattr(old_reader, "args", None) or {}).items()
                     if key in {"client", "max_files"}}
        read_args.update({"from_step": 2, "parse": "auto",
                          "deduplicate_content": logical_dedup})

        semantic_fields = derive_extract_fields(query)
        # A request to audit contradictions needs comparison dimensions that
        # may not be named in the requested output columns.  Keep them as
        # internal extraction fields (the spreadsheet sink remains strictly
        # clause-scoped below) so document variants can be compared on common
        # business facts, not only on amount/date.
        contradiction_audit_fields = []
        if re.search(r"contradditt|contradict|inconsisten|conflict", ql):
            contradiction_audit_fields = ["fornitore", "stato"]
        extract_fields: list[str] = []
        all_extract_fields = (
            list(semantic_fields) + list(sink_fields)
            + ["content_hash", "file_type", "readable"]
        )
        for field in all_extract_fields:
            if field and field not in extract_fields:
                extract_fields.append(field)
        extract_args = {
            "from_step": 3,
            "fields": extract_fields,
            "instruction": query,
            "max_per_text": 20,
            "drill_down": False,
        }
        if contradiction_audit_fields:
            extract_args["audit_fields"] = contradiction_audit_fields

        old_sort = next((step for step in steps
                         if (step.tool or "") == "sort_entries"), None)
        proposed_sort = dict(getattr(old_sort, "args", None) or {}).get("by")
        sort_by = (proposed_sort if proposed_sort in extract_fields else
                   (semantic_fields[0] if semantic_fields else sink_fields[0]))
        result_dir = "${step1.metadata.base_path}/Risultati_Metnos_${RUNTIME:turn_id}"
        canonical = [
            StepSpec(tool="find_files", args=find_args),
            StepSpec(tool="filter_entries", args=filter_args),
            StepSpec(tool="read_files", args=read_args),
            StepSpec(tool="extract_entries", args=extract_args),
            StepSpec(tool="sort_entries", args={
                "from_step": 4, "by": sort_by, "desc": False}),
            StepSpec(tool="describe_entries", args={
                "from_step": 5, "style": "by_relevance", "context": query,
                "data_kind": "entries", "format": "markdown",
                "max_tokens": 1200}),
            StepSpec(tool="create_dirs", args={
                "paths": [result_dir], "parents": True, "exist_ok": False}),
            StepSpec(tool="write_files", args={
                "path": "${step7.results.0.path}/riepilogo.md",
                "content": "${step6.summary}", "mode": "fail_if_exists",
                "client": "local"}),
            StepSpec(tool="create_files_spreadsheet", args={
                "from_step": 5, "columns": list(sink_fields),
                "path": "${step7.results.0.path}/dati_estratti.xlsx",
                "title": "dati_estratti", "client": "local"}),
            StepSpec(tool="find_files", args={
                "base_path": "${step7.results.0.path}",
                "patterns": ["riepilogo.md", "dati_estratti.xlsx"],
                "recursive": False, "client": "local"}),
            StepSpec(tool="compress_files", args={
                "from_step": 10,
                "dest": "${step7.results.0.path}/risultati.zip",
                "format": "zip"}),
            StepSpec(tool="final_answer", args={}),
        ]
        from messages import get as _msg
        normalized = Framework(
            steps=canonical, fillers=dict(getattr(framework, "fillers", {}) or {}),
            final_message=_msg(
                "MSG_DOCUMENT_REPORT_RECEIPT",
                directory="${step7.results.0.path}",
                rows="${step9.results.0.rows}",
                archive="${step11.results.0.path}",
            ),
        )
        if normalized.to_dict() != framework.to_dict():
            log.info("[document_report] pipeline canonicale applicata: %s",
                     [step.tool for step in canonical])
        return normalized
    except Exception as ex:
        log.warning("normalize_document_report_pipeline noop: %r", ex)
        return framework


def _normalize_multisource_entity_report_pipeline(
        framework: Framework, intent, query: str,
        catalog: Optional[list]) -> Framework:
    """Canonical reconciliation for three-or-more heterogeneous sources.

    Supported source families are deliberately capability-based rather than
    query-specific: local/remote files, messages, calendar events and address
    book contacts.  Every observed source is transformed independently into a
    shared record schema before merge.  All durable outputs consume the same
    sorted carrier, use a per-turn create-only directory, and the archive is
    built only from the report and spreadsheet just created.
    """
    try:
        from compound_decomposer import derive_sink_fields
        from ordering_clause import detect as detect_ordering
        from .types import StepSpec

        steps = list(getattr(framework, "steps", None) or [])
        tools = {(getattr(step, "tool", "") or "") for step in steps}
        finder = next((step for step in steps
                       if (step.tool or "") == "find_files"
                       and (step.args or {}).get("base_path")), None)
        reader = next((step for step in steps
                       if (step.tool or "") == "read_files"), None)
        mail = next((step for step in steps
                     if (step.tool or "") == "read_messages"), None)
        events = next((step for step in steps
                       if (step.tool or "") == "read_events"), None)
        contacts = next((step for step in steps
                         if (step.tool or "") == "find_contacts"), None)
        source_count = sum((
            finder is not None and reader is not None,
            mail is not None,
            events is not None,
            contacts is not None,
        ))
        if source_count < 3:
            return framework
        if not ("compress_files" in tools
                and ("create_files_spreadsheet" in tools
                     or re.search(r"\b(?:foglio|spreadsheet|workbook|xlsx)\b",
                                  query or "", re.IGNORECASE))):
            return framework
        forbidden = {
            tool for tool in tools
            if (tool.startswith(("send_", "delete_", "move_", "update_", "set_"))
                or tool in {"create_events", "write_events", "create_contacts",
                            "write_contacts"})
        }
        if forbidden:
            return framework
        # Chat/UI clients may preserve pasted text as a Markdown blockquote.
        # Presentation markers must not split a semantic clause across lines.
        semantic_query = re.sub(r"(?m)^\s*>\s?", "", query or "")
        semantic_query = re.sub(r"\s+", " ", semantic_query).strip()
        ql = semantic_query.casefold()
        if not re.search(
                r"\b(?:analizz\w*|incroci\w*|riconcili\w*|extract\w*|"
                r"estrai\w*|individua\w*|normalizz\w*|conflitt\w*|"
                r"conflict\w*|deduplic\w*)\b", ql):
            return framework

        names = catalog_names(catalog)
        required = {
            "extract_entries", "group_entries", "sort_entries",
            "describe_entries", "create_dirs", "write_files",
            "create_files_spreadsheet", "find_files", "compress_files",
        }
        for producer in (reader, mail, events, contacts):
            if producer is not None:
                required.add(producer.tool)
        if not required <= names:
            return framework

        sink_fields = derive_sink_fields(query)
        default_sheet_fields = [
            "entità", "tipo", "valore normalizzato", "valore originale",
            "dominio", "origine", "responsabile", "confidenza", "conflitto",
        ]
        sheet_fields = list(dict.fromkeys(
            field for field in (sink_fields or default_sheet_fields) if field))
        if len(sheet_fields) < 2:
            sheet_fields = default_sheet_fields
        # User-facing spreadsheet headers may legitimately be plural because
        # reconciliation preserves several source domains/origins.  Extraction
        # and merge, however, own one canonical singular field for each
        # concept.  Do not ask the LLM for parallel plural fields: they become
        # empty competitors of the populated canonical values at the sink.
        record_field_aliases = {
            "domini": "dominio", "domains": "dominio",
            "origini": "origine", "origins": "origine",
        }
        record_sink_fields = [
            record_field_aliases.get(field.casefold(), field)
            for field in sheet_fields
        ]
        common_fields = list(dict.fromkeys([
            "entità", "tipo", "valore normalizzato", "valore originale",
            "progetto", "organizzazione", "ruolo", "email", "telefono",
            "importo", "scadenza", "decisione", "stato", "origine",
            "responsabile", "confidenza", "dominio", "leggibile",
            "duplicati", "diagnostica",
            *[field for field in record_sink_fields if field != "conflitto"],
        ]))
        extract_instruction = (
            "Per ciascuna sorgente estrai record separati per persone, "
            "organizzazioni, progetti, ruoli, recapiti, importi, scadenze, "
            "decisioni e impegni realmente citati. Normalizza nomi, email, "
            "telefoni, date, valute e stati. Il campo entità identifica il "
            "soggetto o fatto specifico, mai una label generica. Quando "
            "importo, scadenza, decisione, stato o responsabile sono "
            "associati esplicitamente a un progetto o impegno, mantienili "
            "nello stesso record del soggetto: non trasformarli in entità "
            "autonome. Usa la stessa entità e lo stesso tipo per lo stesso "
            "soggetto citato in sorgenti diverse. Non confrontare sorgenti "
            "e non creare output."
        )
        extraction_base = {
            "fields": common_fields,
            "instruction": extract_instruction,
            "max_per_text": 12,
            "max_total": 5000,
            "max_sources": 1000,
            "batch_size": 16,
            "drill_down": False,
        }

        canonical: list[StepSpec] = []
        source_positions: list[tuple[str, int]] = []
        scope_relevance_terms: list[str] = []

        def append_step(tool: str, args: dict) -> int:
            canonical.append(StepSpec(tool=tool, args=args))
            return len(canonical)

        if finder is not None and reader is not None:
            allowed_find = {
                "base_path", "pattern", "patterns", "recursive",
                "max_results", "max_depth", "case_sensitive", "client",
            }
            find_args = {
                key: value for key, value in dict(finder.args or {}).items()
                if key in allowed_find
            }
            find_args.setdefault("recursive", True)
            scope_path = str(find_args.get("base_path") or "")
            scope_leaf = re.split(r"[/\\]+", scope_path.rstrip("/\\"))[-1]
            scope_tokens = re.findall(r"[\wÀ-ÿ@.+-]+", scope_leaf)
            generic_scope_tokens = {
                "documenti", "documents", "document", "progetto", "project",
                "cartella", "folder", "files", "file",
            }
            scope_relevance_terms = list(dict.fromkeys([
                scope_leaf,
                *[token for token in scope_tokens
                  if len(token) >= 4
                  and token.casefold() not in generic_scope_tokens],
            ]))
            file_find_pos = append_step("find_files", find_args)
            read_args = {
                key: value for key, value in dict(reader.args or {}).items()
                if key in {"client", "max_files"}
            }
            read_args.update({
                "from_step": file_find_pos,
                "parse": "auto",
                "deduplicate_content": True,
            })
            source_positions.append(("files", append_step("read_files", read_args)))

        if mail is not None:
            mail_allowed = {
                "account", "folder", "max_results", "unseen_only",
                "time_window", "since", "before", "from_contains",
                "subject_contains", "body_contains", "max_total",
                "page_size", "via_channel", "client",
            }
            mail_args = {
                key: value for key, value in dict(mail.args or {}).items()
                if key in mail_allowed
            }
            mail_args.setdefault("account", "all")
            mail_args.setdefault("max_total", 500)
            mail_args.setdefault("page_size", 100)
            source_positions.append((
                "email", append_step("read_messages", mail_args)))

        if events is not None:
            event_allowed = {
                "time_window", "start", "end", "top_k", "calendar_id",
                "client",
            }
            event_args = {
                key: value for key, value in dict(events.args or {}).items()
                if key in event_allowed
            }
            event_args.setdefault("top_k", 500)
            source_positions.append((
                "calendar", append_step("read_events", event_args)))

        if contacts is not None:
            contact_args = {
                key: value for key, value in dict(contacts.args or {}).items()
                if key in {"query", "max_results", "client"}
            }
            contact_args.setdefault("query", "")
            # In this pipeline the address book is a reference registry.  The
            # provider contract documents the empty query as "all contacts";
            # a planner-emitted universal wildcard is a literal substring for
            # find_contacts and otherwise returns only names containing '*'.
            if str(contact_args.get("query") or "").strip() in {"*", "**"}:
                contact_args["query"] = ""
            contact_args.setdefault("max_results", 1000)
            source_positions.append((
                "contacts", append_step("find_contacts", contact_args)))

        extracted_positions: list[int] = []
        file_extract_position: int | None = None
        for domain, producer_position in source_positions:
            if domain == "contacts":
                contact_fields = list(common_fields)
                args = {
                    "from_step": producer_position,
                    "fields": contact_fields,
                    "max_sources": 1000,
                    "max_total": 1000,
                    "drill_down": False,
                    "structured_map": {
                        "entità": ["name", "id"],
                        "valore normalizzato": ["name", "id"],
                        "valore originale": ["name", "id"],
                        "email": "emails",
                        "telefono": "phones",
                    },
                    "structured_defaults": {"tipo": "contatto"},
                }
            else:
                args = {**extraction_base, "from_step": producer_position}
                if domain != "files" and file_extract_position is not None:
                    # Il corpus file è la sorgente-ancora circoscritta dalla
                    # query. I producer voluminosi vengono prefiltrati sulle
                    # entità già osservate prima di spendere chiamate LLM.
                    args.update({
                        "relevance_entries": (
                            f"${{step{file_extract_position}.entries}}"),
                        "relevance_fields": [
                            "entità", "progetto", "organizzazione",
                            "email", "telefono",
                        ],
                        "relevance_terms": scope_relevance_terms,
                    })
            extract_position = append_step("extract_entries", args)
            extracted_positions.append(extract_position)
            if domain == "files":
                file_extract_position = extract_position

        merge_position = append_step("group_entries", {
            "entries_lists": [
                f"${{step{position}.entries}}"
                for position in extracted_positions
            ],
            "dedup_key": ["entità", "tipo", "valore normalizzato"],
            "cross_domain_key": "entità",
            "domain_field": "dominio",
            "cross_match_fields": [
                "tipo", "valore normalizzato", "email", "telefono",
                "scadenza", "importo",
            ],
            "merge_fields": ["origine", "dominio", "duplicati"],
            "conflict_fields": [
                "organizzazione", "ruolo", "email", "telefono", "importo",
                "scadenza", "decisione", "stato", "responsabile",
            ],
            "conflict_field": "conflitto",
            "reconcile_within_domains": ["files"],
            "drop_unmatched_domains": ["contacts"],
            # A small model may atomize source-level amount/deadline/state
            # facts even when the text associates all of them with one
            # project.  Coalesce only when that source has exactly one
            # declared subject; ambiguous multi-subject sources stay intact.
            "coalesce_source_facts": True,
            "source_field": "origine",
            "type_field": "tipo",
            "subject_types": ["progetto", "project", "impegno", "commitment"],
            "coalesce_fields": [
                "organizzazione", "importo", "scadenza", "decisione",
                "stato", "responsabile",
            ],
        })
        deadline_position = append_step("sort_entries", {
            "from_step": merge_position,
            "by": "scadenza",
            "desc": False,
            "value_type": "date",
        })
        ordering = detect_ordering(semantic_query) or {}
        conflict_severity_requested = bool(re.search(
            r"(?:\bgravit[aà]\s+(?:(?:del|della|dei|delle|di)\s+)?"
            r"conflitt\w*\b|\bconflict\s+severity\b|"
            r"\bseverity\s+(?:of\s+)?conflicts?\b)",
            ql,
        ))
        primary = ("_conflict_count" if conflict_severity_requested else
                   str(ordering.get("key_text") or "organizzazione"))
        primary_aliases = {
            "organization": "organizzazione",
            "organizzazioni": "organizzazione",
            "project": "progetto",
            "projects": "progetto",
            "progetti": "progetto",
            "deadline": "scadenza",
            "date": "scadenza",
        }
        primary = primary_aliases.get(primary.casefold(), primary)
        if primary != "_conflict_count" and primary not in common_fields:
            primary = "organizzazione"
        if primary == "scadenza":
            sorted_position = deadline_position
        else:
            sorted_position = append_step("sort_entries", {
                "from_step": deadline_position,
                "by": primary,
                "desc": (True if primary == "_conflict_count" else
                         bool(ordering.get("desc", False))),
                "value_type": "auto",
            })
        report_position = append_step("describe_entries", {
            "from_step": sorted_position,
            "style": "compact",
            "context": query,
            "data_kind": "entries",
            "format": "markdown",
            # Severity is a ranking criterion, not a user-visible grouping
            # dimension.  The technical key remains hidden from the report.
            "group_by": "" if primary == "_conflict_count" else primary,
        })

        old_directory = next((step for step in steps
                              if (step.tool or "") == "create_dirs"), None)
        old_paths = ((old_directory.args or {}).get("paths")
                     if old_directory is not None else None)
        base_directory = (old_paths[0] if isinstance(old_paths, list)
                          and old_paths and isinstance(old_paths[0], str)
                          else "Documenti/Verifica Metnos")
        base_directory = base_directory.rstrip("/\\")
        if "${RUNTIME:turn_id}" in base_directory:
            result_dir = base_directory
        else:
            result_dir = base_directory + "/Run_${RUNTIME:turn_id}"
        sink_client = ((old_directory.args or {}).get("client")
                       if old_directory is not None else None) or "local"
        directory_position = append_step("create_dirs", {
            "paths": [result_dir], "parents": True, "exist_ok": False,
            "client": sink_client,
        })
        report_name = "rapporto_riconciliazione.md"
        sheet_name = "entita_riconciliate.xlsx"
        archive_name = "risultati_riconciliazione.zip"
        is_italian = bool(re.search(
            r"\b(?:incrocia|esamina|ultimi|estrai|crea|cartella|foglio|non)\b",
            ql))
        coverage_lines = []
        for (domain, producer_position), extract_position in zip(
                source_positions, extracted_positions):
            if domain == "contacts":
                if is_italian:
                    coverage_lines.append(
                        f"- {domain}: "
                        f"${{step{producer_position}.used}}/"
                        f"${{step{producer_position}.available_total}} "
                        "record di riferimento letti; "
                        f"${{step{extract_position}.used}} "
                        "record normalizzati"
                    )
                else:
                    coverage_lines.append(
                        f"- {domain}: "
                        f"${{step{producer_position}.used}}/"
                        f"${{step{producer_position}.available_total}} "
                        "reference records read; "
                        f"${{step{extract_position}.used}} "
                        "records normalized"
                    )
                continue
            coverage_lines.append(
                f"- {domain}: "
                f"${{step{extract_position}.selected_source_total}}/"
                f"${{step{extract_position}.input_source_total}} "
                + ("sorgenti pertinenti; " if is_italian
                   else "relevant sources; ")
                + f"${{step{extract_position}.used}} "
                + ("record prodotti" if is_italian else "records produced")
            )
        if is_italian:
            report_content = (
                "# Rapporto di riconciliazione\n\n## Copertura e controlli\n"
                + "\n".join(coverage_lines)
                + f"\n- conflitti rilevati: ${{step{merge_position}.conflicts}}"
                + "\n- record di riferimento non associati esclusi: "
                + f"${{step{merge_position}.dropped_unmatched}}\n\n"
                + f"${{step{report_position}.summary}}"
            )
        else:
            report_content = (
                "# Reconciliation report\n\n## Coverage and checks\n"
                + "\n".join(coverage_lines)
                + f"\n- conflicts found: ${{step{merge_position}.conflicts}}"
                + "\n- unmatched reference records excluded: "
                + f"${{step{merge_position}.dropped_unmatched}}\n\n"
                + f"${{step{report_position}.summary}}"
            )
        append_step("write_files", {
            "path": f"${{step{directory_position}.results.0.path}}/{report_name}",
            "content": report_content,
            "mode": "fail_if_exists",
            "client": sink_client,
        })
        sheet_position = append_step("create_files_spreadsheet", {
            "from_step": sorted_position,
            "columns": sheet_fields,
            "path": f"${{step{directory_position}.results.0.path}}/{sheet_name}",
            "title": "entita_riconciliate",
            "client": sink_client,
        })
        outputs_position = append_step("find_files", {
            "base_path": f"${{step{directory_position}.results.0.path}}",
            "patterns": [report_name, sheet_name],
            "recursive": False,
            "client": sink_client,
        })
        archive_position = append_step("compress_files", {
            "from_step": outputs_position,
            "dest": f"${{step{directory_position}.results.0.path}}/{archive_name}",
            "format": "zip",
        })
        append_step("final_answer", {})

        if is_italian:
            final_message = (
                f"Completato. Risultati salvati in "
                f"`${{step{directory_position}.results.0.path}}`:\n\n"
                f"- rapporto: `{report_name}`\n"
                f"- foglio dati: `{sheet_name}` "
                f"(${{step{sorted_position}.count}} righe di dati)\n"
                f"- archivio: `${{step{archive_position}.results.0.path}}`"
            )
        else:
            final_message = (
                f"Completed. Results saved in "
                f"`${{step{directory_position}.results.0.path}}`:\n\n"
                f"- report: `{report_name}`\n"
                f"- data spreadsheet: `{sheet_name}` "
                f"(${{step{sorted_position}.count}} data rows)\n"
                f"- archive: `${{step{archive_position}.results.0.path}}`"
            )
        normalized = Framework(
            steps=canonical,
            fillers=dict(getattr(framework, "fillers", {}) or {}),
            final_message=final_message,
            runtime_step_cap=len(canonical),
        )
        if normalized.to_dict() != framework.to_dict():
            log.info("[multisource_entity_report] canonical pipeline applied: %s",
                     [step.tool for step in canonical])
        return normalized
    except Exception as ex:
        log.warning("normalize_multisource_entity_report_pipeline noop: %r", ex)
        return framework


def _semantic_query_text(query: str) -> str:
    """Remove Markdown quote presentation without changing comparisons.

    HTTP clients can preserve a quoted multi-line prompt or flatten it before
    deterministic guards run.  Once a query is known to be a Markdown quote,
    standalone ``>`` tokens are presentation, including the flattened ones.
    An ordinary query such as ``importo > 100`` is left untouched.
    """
    raw = str(query or "")
    markdown_quote = bool(re.search(r"(?m)^\s*>", raw))
    if markdown_quote:
        raw = re.sub(r"(?<!\S)>(?=\s|$)\s*", "", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _message_event_focus_terms(query: str) -> list[str]:
    """Extract an explicit bounded subject list from a reconciliation clause.

    This is intentionally narrower than generic keyword extraction.  A broad
    mail/calendar report ("all commitments in both domains") must keep full
    recall, while "commitments related to A, B or C" declares a real source
    scope that can be applied before any LLM call.  The returned phrases stay
    intact; ``extract_entries`` owns Unicode normalization and word-boundary
    matching.
    """
    semantic_query = _semantic_query_text(query)
    if not semantic_query:
        return []
    focus_pattern = re.compile(
        r"\b(?:relativ[ei]?\s+a|riguardant[ei]?|concernent[ei]?|"
        r"related\s+to|concerning|regarding)\s+(.{1,500}?)(?=[.;]|$)",
        re.IGNORECASE,
    )
    match = focus_pattern.search(semantic_query)
    if not match:
        return []
    clause = (match.group(1) or "").strip(" :,-")
    parts = re.split(
        r"\s*,\s*|\s+(?:o|od|oppure|e|ed|or|and)\s+", clause,
        flags=re.IGNORECASE,
    )
    articles = re.compile(
        r"^(?:(?:il|lo|la|i|gli|le|un|uno|una|the|a|an)\s+)+",
        re.IGNORECASE,
    )
    generic = {
        "email", "mail", "messaggi", "messages", "eventi", "events",
        "appuntamenti", "appointments", "impegni", "commitments",
    }
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        term = articles.sub("", part).strip(" :,-")
        folded = term.casefold()
        if (not term or len(term) > 100 or folded in generic
                or folded in seen):
            continue
        seen.add(folded)
        terms.append(term)
    return terms[:32]


def _normalize_message_event_report_pipeline(framework: Framework, intent,
                                              query: str,
                                              catalog: Optional[list]) -> Framework:
    """Canonical cross-domain mail/calendar analysis with durable outputs.

    This deliberately triggers from both the requested shape and the observed
    producers.  It cannot turn a single-domain request into a cross-domain one,
    and it never introduces an outbound or calendar mutation.  The common
    schema is owned here so extraction, merge, report and spreadsheet cannot
    silently drift to incompatible fields.
    """
    try:
        from compound_decomposer import derive_sink_fields
        from .types import StepSpec

        steps = list(getattr(framework, "steps", None) or [])
        tools = {(getattr(step, "tool", "") or "") for step in steps}
        if tools & {"read_files", "find_contacts"}:
            return framework
        if not {"read_messages", "read_events"} <= tools:
            return framework
        if not ("create_files_spreadsheet" in tools
                or re.search(r"\b(?:foglio|spreadsheet|workbook|xlsx)\b",
                             query or "", re.IGNORECASE)):
            return framework
        # A report normalizer must never absorb a genuinely mutating request.
        # Negative clauses such as "non inviare" do not create such a tool, so
        # checking the executable plan is both narrower and negation-safe.
        forbidden = {
            tool for tool in tools
            if (tool.startswith(("send_", "delete_", "move_", "update_"))
                or tool in {"create_events", "write_events", "set_events"})
        }
        if forbidden:
            return framework

        semantic_query = _semantic_query_text(query)
        ql = semantic_query.casefold()
        if not re.search(
                r"\b(?:analizz\w*|extract\w*|estrai\w*|individua\w*|"
                r"normalizz\w*|conflitt\w*|conflict\w*|deduplic\w*)\b",
                ql):
            return framework

        focus_terms = _message_event_focus_terms(query)
        wants_archive = bool(re.search(
            r"\b(?:zip|archivio\s+compress\w*|compressed\s+archive)\b",
            ql, re.IGNORECASE))
        focused_reconciliation = bool(focus_terms) or bool(re.search(
            r"\b(?:corrispondenz\w*\s+(?:esatt\w*|probabil\w*)|"
            r"exact\s+match|probable\s+match|solo\s+(?:email|calendario)|"
            r"only\s+(?:email|calendar)|cancellazion\w*\s+priv\w*)\b",
            ql, re.IGNORECASE))

        names = catalog_names(catalog)
        required = {
            "read_messages", "read_events", "extract_entries",
            "group_entries", "sort_entries", "describe_entries",
            "create_dirs", "write_files", "create_files_spreadsheet",
        }
        if wants_archive:
            required.update({"find_files", "compress_files"})
        if not required <= names:
            return framework

        old_mail = next(step for step in steps
                        if (step.tool or "") == "read_messages")
        old_events = next(step for step in steps
                          if (step.tool or "") == "read_events")
        mail_allowed = {
            "account", "folder", "max_results", "unseen_only", "time_window",
            "since", "before", "from_contains", "subject_contains",
            "body_contains", "max_total", "page_size", "via_channel", "client",
        }
        event_allowed = {
            "time_window", "start", "end", "top_k", "calendar_id", "client",
        }
        mail_args = {
            key: value for key, value in dict(old_mail.args or {}).items()
            if key in mail_allowed
        }
        event_args = {
            key: value for key, value in dict(old_events.args or {}).items()
            if key in event_allowed
        }
        mail_args.setdefault("account", "all")
        mail_args.setdefault("max_total", 500)
        mail_args.setdefault("page_size", 100)
        event_args.setdefault("top_k", 500)

        default_sheet_fields = [
            "entità", "valore normalizzato", "valore originale", "origine",
            "responsabile", "confidenza", "conflitto",
        ]
        requested_sheet_fields = derive_sink_fields(query)
        sheet_columns = list(dict.fromkeys(
            field for field in (requested_sheet_fields or default_sheet_fields)
            if field))
        field_aliases = {
            "domini": "dominio", "domains": "dominio",
            "origini": "origine", "origins": "origine",
        }
        record_sheet_fields = [
            field_aliases.get(field.casefold(), field)
            for field in sheet_columns
        ]

        if focused_reconciliation:
            common_fields = list(dict.fromkeys([
                "entità", "tipo impegno", "persona", "organizzazione",
                "data normalizzata", "ora normalizzata", "fuso orario",
                "luogo", "valore originale", "stato", "responsabile",
                "origine", "confidenza", "dominio",
                *[field for field in record_sheet_fields
                  if field not in {"conflitto", "corrispondenza"}],
            ]))
            focus_text = "; ".join(focus_terms)
            extract_instruction = (
                "Estrai soltanto gli impegni pertinenti al focus esplicito"
                + (f" ({focus_text}). " if focus_text else ". ")
                + "Produci un record per ogni impegno distinto, non record "
                "separati per persona o organizzazione. Usa entità come "
                "soggetto normalizzato e stabile dell'impegno e tipo impegno "
                "come categoria normalizzata della prestazione o attività, "
                "così lo stesso impegno mantiene gli stessi valori in email "
                "e calendario. Normalizza data in ISO YYYY-MM-DD, ora in "
                "HH:MM, fuso orario, nomi e stati; conserva il testo osservato "
                "in valore originale. Se ora, luogo o altro dato non sono "
                "presenti, lascia il campo vuoto: non inventare. Non fondere "
                "impegni distinti e non confrontare sorgenti."
            )
            merge_args = {
                "entries_lists": ["${step3.entries}", "${step4.entries}"],
                "dedup_key": [
                    "entità", "tipo impegno", "data normalizzata",
                    "ora normalizzata",
                ],
                "cross_domain_key": [
                    "entità", "tipo impegno", "data normalizzata",
                ],
                "domain_field": "dominio",
                "cross_match_fields": [
                    "ora normalizzata", "organizzazione", "persona", "luogo",
                ],
                "merge_fields": ["origine", "dominio"],
                "conflict_fields": [
                    "ora normalizzata", "luogo", "stato", "organizzazione",
                ],
                "missing_conflict_fields": ["ora normalizzata"],
                "missing_value_label": "mancante",
                "required_fields_by_domain": {
                    "calendar": ["ora normalizzata"],
                },
                "unmatched_conflict_key": ["entità", "tipo impegno"],
                "unmatched_conflict_fields": ["data normalizzata"],
                "conflict_field": "conflitto",
                "match_field": "corrispondenza",
                "match_labels": {
                    "exact": "corrispondenza esatta",
                    "probable": "corrispondenza probabile",
                    "email_only": "solo email",
                    "calendar_only": "solo calendario",
                    "cancelled": "cancellazione senza evento",
                    "unmatched": "non riconciliato",
                },
                "match_state_field": "stato",
                "cancellation_states": [
                    "annullato", "annullata", "cancellato", "cancellata",
                    "cancelled", "canceled",
                ],
                # Model-normalized identity can drift between a person and
                # the appointment label.  These private runtime facts allow
                # a conservative second-stage join: same date + shared
                # observed focus anchor + one unique best evidence score.
                "anchor_field": "_relevance_anchors",
                "anchor_equal_fields": ["data normalizzata"],
                "anchor_match_fields": [
                    "_source_time_mentions", "organizzazione",
                    "tipo impegno", "persona", "entità",
                ],
                "anchor_within_domains": ["email"],
            }
            date_field = "data normalizzata"
            report_name = "rapporto_riconciliazione.md"
            sheet_name = "impegni_riconciliati.xlsx"
            sheet_title = "impegni_riconciliati"
        else:
            common_fields = list(dict.fromkeys([
                "entità", "tipo", "valore normalizzato",
                "valore originale", "origine", "responsabile", "confidenza",
                "scadenza", "stato", "dominio",
                *[field for field in record_sheet_fields
                  if field != "conflitto"],
            ]))
            extract_instruction = (
                "Per ciascuna sorgente estrai separatamente ogni persona, "
                "organizzazione, scadenza, importo e impegno citato. "
                "Normalizza nomi, indirizzi email, date, valute e stati. "
                "Il campo entità deve identificare il soggetto o fatto "
                "specifico, mai una label generica. Non confrontare sorgenti "
                "e non creare output."
            )
            merge_args = {
                "entries_lists": ["${step3.entries}", "${step4.entries}"],
                "dedup_key": ["entità", "valore normalizzato"],
                "cross_domain_key": "entità",
                "domain_field": "dominio",
                "cross_match_fields": [
                    "valore normalizzato", "scadenza", "valore originale",
                ],
                "merge_fields": ["origine", "dominio"],
                "conflict_fields": [
                    "valore normalizzato", "valore originale", "scadenza",
                    "stato", "responsabile",
                ],
                "conflict_field": "conflitto",
            }
            date_field = "scadenza"
            report_name = "rapporto_scadenze.md"
            sheet_name = "entita_valori.xlsx"
            sheet_title = "entita_valori"

        extract_base = {
            "fields": common_fields,
            "instruction": extract_instruction,
            "max_per_text": 8,
            "max_total": 2000,
            "max_sources": 500,
            "batch_size": 16,
            "drill_down": False,
        }
        if focus_terms:
            extract_base["relevance_terms"] = focus_terms
        if focused_reconciliation:
            extract_base["state_markers"] = {
                "annullato": [
                    "annullamento prenotazione", "e stato annullato",
                    "e stata annullata", "appuntamento annullato",
                    "appuntamento annullata", "cancellazione prenotazione",
                    "cancelled", "canceled",
                ],
            }

        result_root = "Documenti/Verifica Metnos"
        old_directory = next((
            step for step in steps if (step.tool or "") == "create_dirs"
        ), None)
        old_paths = dict(getattr(old_directory, "args", None) or {}).get("paths")
        if (isinstance(old_paths, list) and old_paths
                and isinstance(old_paths[0], str) and old_paths[0].strip()):
            result_root = old_paths[0].rstrip("/\\")
        result_dir = result_root
        if "${RUNTIME:turn_id}" not in result_dir:
            result_dir += "/Run_${RUNTIME:turn_id}"

        canonical: list[StepSpec] = []

        def append_step(tool: str, args: dict) -> int:
            canonical.append(StepSpec(tool=tool, args=args))
            return len(canonical)

        mail_pos = append_step("read_messages", mail_args)
        events_pos = append_step("read_events", event_args)
        mail_extract_pos = append_step("extract_entries", {
            **extract_base, "from_step": mail_pos})
        event_extract_pos = append_step("extract_entries", {
            **extract_base, "from_step": events_pos})
        merge_args["entries_lists"] = [
            f"${{step{mail_extract_pos}.entries}}",
            f"${{step{event_extract_pos}.entries}}",
        ]
        merge_pos = append_step("group_entries", merge_args)
        date_sort_pos = append_step("sort_entries", {
            "from_step": merge_pos, "by": date_field, "desc": False,
            "value_type": "date",
        })
        carrier_pos = date_sort_pos
        if focused_reconciliation:
            carrier_pos = append_step("sort_entries", {
                "from_step": date_sort_pos, "by": "_conflict_count",
                "desc": True, "value_type": "auto",
            })
        describe_args = {
            "from_step": carrier_pos, "style": "compact", "context": query,
            "data_kind": "entries", "format": "markdown",
        }
        if not focused_reconciliation:
            describe_args["group_by"] = "scadenza"
        describe_pos = append_step("describe_entries", describe_args)
        directory_pos = append_step("create_dirs", {
            "paths": [result_dir], "parents": True, "exist_ok": False,
            "client": "local",
        })
        append_step("write_files", {
            "path": f"${{step{directory_pos}.results.0.path}}/{report_name}",
            "content": f"${{step{describe_pos}.summary}}",
            "mode": "fail_if_exists", "client": "local",
        })
        append_step("create_files_spreadsheet", {
            "from_step": carrier_pos, "columns": sheet_columns,
            "path": f"${{step{directory_pos}.results.0.path}}/{sheet_name}",
            "title": sheet_title, "client": "local",
        })
        archive_pos = 0
        if wants_archive:
            artifacts_pos = append_step("find_files", {
                "base_path": f"${{step{directory_pos}.results.0.path}}",
                "patterns": [report_name, sheet_name], "recursive": False,
                "client": "local",
            })
            archive_pos = append_step("compress_files", {
                "from_step": artifacts_pos,
                "dest": (f"${{step{directory_pos}.results.0.path}}/"
                         "risultati_riconciliazione.zip"),
                "format": "zip",
            })
        append_step("final_answer", {})
        is_italian = bool(re.search(
            r"\b(?:analizza|ultimi|individua|crea|cartella|foglio|non)\b",
            ql))
        if is_italian:
            final_message = (
                f"Completato. Risultati salvati in "
                f"`${{step{directory_pos}.results.0.path}}`:\n\n"
                f"- rapporto: `{report_name}`\n"
                f"- foglio dati: `{sheet_name}` "
                f"(${{step{carrier_pos}.count}} righe di dati)"
            )
            if archive_pos:
                final_message += (
                    f"\n- archivio: `${{step{archive_pos}.results.0.path}}`")
        else:
            final_message = (
                f"Completed. Results saved in "
                f"`${{step{directory_pos}.results.0.path}}`:\n\n"
                f"- report: `{report_name}`\n"
                f"- data spreadsheet: `{sheet_name}` "
                f"(${{step{carrier_pos}.count}} data rows)"
            )
            if archive_pos:
                final_message += (
                    f"\n- archive: `${{step{archive_pos}.results.0.path}}`")
        normalized = Framework(
            steps=canonical,
            fillers=dict(getattr(framework, "fillers", {}) or {}),
            final_message=final_message,
            runtime_step_cap=len(canonical),
        )
        if normalized.to_dict() != framework.to_dict():
            log.info("[message_event_report] canonical pipeline applied: %s",
                     [step.tool for step in canonical])
        return normalized
    except Exception as ex:
        log.warning("normalize_message_event_report_pipeline noop: %r", ex)
        return framework


@dataclass(frozen=True)
class Guard:
    """Un guard deterministico di struttura, con metadati DICHIARATI (PROV.1,
    architettura provenienza args). `fn(fw, intent, query, catalog) -> fw`
    invariata. `scope` classifica il guard (per la lettura + i test di
    non-collisione); `writes`/`reads` dichiarano cosa tocca/legge (nomi-campo
    `args.<x>`/`step.tool`/`step`), incrociabili con `arg_provenance`. Zero
    cambio di comportamento: è documentazione tipizzata + verificabile."""
    name: str
    fn: Callable
    v3_only: bool = False
    scope: str = "structure"        # per-clause | cross-clause | structure | routing
    writes: frozenset = frozenset()
    reads: frozenset = frozenset()
    rationale: str = ""
    adr: str = ""


GUARD_PIPELINE: tuple = (
    Guard("coerce_args_to_schema",
          lambda fw, i, q, c: _coerce_args_to_schema(fw, c),
          v3_only=True, scope="structure", writes=frozenset({"args.*"}),
          reads=frozenset({"catalog"}),
          rationale="FASE 3.1 provenienza: backstop unico sul confine LLM→pipeline — drop chiavi fuori-schema e leak runtime_resolved, enum case-normalize o drop (mai snap). PRIMO per costruzione: tocca solo l'output grezzo del proposer, i guard a valle scrivono dopo",
          adr="0177"),
    Guard("overwrite_phantom_install_args",
          lambda fw, i, q, c: _overwrite_phantom_install_args(fw, q),
          scope="structure", writes=frozenset({"args.base_path", "args.path"}),
          reads=frozenset({"query"}),
          rationale="rimuove base_path/path install-root non nominati dalla query (default appreso avvelenato / cache stantia). Cat. C spec provenienza: rimovibile con evidenza journal «[phantom_install]» = 0 fire su >=14 giorni di traffico reale (finestra aperta 7/7/2026, verifica >=21/7)",
          adr="0182"),
    Guard("align_framework_objects",
          lambda fw, i, q, c: _align_framework_objects(fw, i, c),
          scope="structure", writes=frozenset({"step.tool"}),
          reads=frozenset({"intent", "catalog"}),
          rationale="allinea l'oggetto degli step all'intent (files↔dirs equivalence)",
          adr="0177"),
    Guard("route_text_web_image_search",
          _route_text_web_image_search,
          scope="routing", writes=frozenset({"step", "args.queries"}),
          reads=frozenset({"intent", "query", "step.tool"}),
          rationale="separa text→web da image→Vision: una richiesta web image-only senza sorgente/reverse non puo usare il corpus locale",
          adr="image-web-modes"),
    Guard("enforce_missing_clauses",
          lambda fw, i, q, c: _enforce_missing_clauses(fw, i, q, c),
          scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"intent.actions", "catalog", "query"}),
          rationale="appende il produttore per un verbo RICHIESTO scoperto (usa _align_foreign_producers_v3 come helper interno)",
          adr="0177"),
    Guard("enforce_missing_objects",
          lambda fw, i, q, c: _enforce_missing_objects(fw, i, q, c),
          v3_only=True, scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"intent.actions", "catalog"}),
          rationale="appende il produttore per un OGGETTO scoperto (drop per-object che il verb-level non vede)",
          adr="0177"),
    Guard("ensure_site_session_precursor",
          lambda fw, i, q, c: _ensure_site_session_precursor(fw, i, q, c),
          scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"intent.actions", "query", "step.tool"}),
          rationale="spec sites F1/F2: login/read/act_sites richiedono open_sites; ricostruisce open→[login]→[read]→[act] preservando action/value_ref",
          adr="sites-F2"),
    Guard("decontaminate_reader_qualifier",
          lambda fw, i, q, c: _decontaminate_reader_qualifier(fw, q, c),
          v3_only=True, scope="cross-clause", writes=frozenset({"step.tool"}),
          reads=frozenset({"query", "catalog"}),
          rationale="demote read_<obj>_<fmt>→read_<obj> quando il formato è contaminato da una clausola-sink a valle",
          adr="0174"),
    Guard("ensure_extract_clause",
          lambda fw, i, q, c: _ensure_extract_clause(fw, i, q, c),
          v3_only=True, scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"intent.actions", "query"}),
          rationale="inserisce lo step extract mancante fra read e create (compound)",
          adr="0174"),
    Guard("ensure_extracted_period_scope",
          lambda fw, i, q, c: _ensure_extracted_period_scope(fw, i, q, c),
          v3_only=True, scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"query", "step"}),
          rationale="dopo extract applica deterministicamente gli anni espliciti della query su un campo data/anno, ricablando i consumer",
          adr="0174"),
    Guard("conform_to_intent_order",
          lambda fw, i, q, c: _conform_to_intent_order(fw, i, q, c),
          v3_only=True, scope="structure", writes=frozenset({"step"}),
          reads=frozenset({"intent.actions"}),
          rationale="riordina gli step nell'ordine di intent.actions",
          adr="0177"),
    Guard("scope_dirs_clause_to_contents",
          lambda fw, i, q, c: _scope_dirs_clause_to_contents(fw, i, q, c),
          v3_only=True, scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"catalog"}),
          rationale="§2.9: delete_dirs sul contenitore X (clausola file fratella) → find_dirs(base=X)→delete_dirs(from_step): scopa alle sottodir, X resta (no over-deletion). Dopo conform_to_intent_order: l'ordine appeso è finale",
          adr="0177"),
    Guard("enrich_move_source_dir",
          lambda fw, i, q, c: _enrich_move_source_dir(fw, q, c),
          v3_only=True, scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"query", "catalog"}),
          rationale="§7.9 move-enumeration: «sposta i file DA cartella X» (lessico fs.files_in_folder) con move_files(entries=[dir]) → find_files(base=X)→move(from_step). Solo move (mai delete); «sposta la cartella X» non matcha",
          adr="0177"),
    Guard("fill_clause_args",
          lambda fw, i, q, c: _fill_clause_args(fw, i, q, c),
          v3_only=True, scope="per-clause", writes=frozenset({"args.*"}),
          reads=frozenset({"clause", "catalog"}),
          rationale="riempie gli args deducibili dal chunk della clausola (pattern/date/store); NON sovrascrive l'LLM. È LO STAGE clause-derive (esito PROV.3 6/7: già ben costruito, count-cap nidificati; non sussumere)",
          adr="0177"),
    Guard("normalize_result_folder_exclusion",
          lambda fw, i, q, c: _normalize_result_folder_exclusion(fw, q, c),
          scope="per-clause",
          writes=frozenset({"args.name_regex", "args.where_field",
                            "args.where_regex", "args.from_step"}),
          reads=frozenset({"query", "step.tool"}),
          rationale="esclusione esplicita Risultati_Metnos_*: il predicato deve operare sul path completo, mai sul solo nome file; ripara anche alterazioni ortografiche del token",
          adr="0177"),
    Guard("resolve_store_field_refs",
          lambda fw, i, q, c: _resolve_store_field_refs(fw),
          v3_only=True, scope="structure", writes=frozenset({"args.*"}),
          reads=frozenset({"step"}),
          rationale="risolve i riferimenti ${stepN.field} negli args (contesto-turno, resta)",
          adr="0177"),
    Guard("route_mail_delete_to_trash",
          lambda fw, i, q, c: _route_mail_delete_to_trash(fw, c),
          scope="routing", writes=frozenset({"step.tool", "args.dst_folder"}),
          reads=frozenset({"catalog"}),
          rationale="delete_messages→move_messages(dst=Trash) (mail non ha delete, §5)",
          adr="0177"),
    Guard("route_filename_pattern_to_find",
          lambda fw, i, q, c: _route_filename_pattern_to_find(fw, q, c),
          scope="routing", writes=frozenset({"step.tool", "args.pattern"}),
          reads=frozenset({"query", "catalog"}),
          rationale="instrada un pattern-nomefile allo step find",
          adr="0177"),
    Guard("align_provider_client",
          lambda fw, i, q, c: _align_provider_client(fw, q, c),
          scope="cross-clause", writes=frozenset({"args.client"}),
          reads=frozenset({"query", "catalog"}),
          rationale="allinea l'arg client/provider. NB (PROV.3): client è clause-derived sui tool multi-provider (da testo→gw), NON runtime puro — non sussumibile da runtime-resolve",
          adr="0136"),
    Guard("scope_sink_provider_to_clause",
          lambda fw, i, q, c: _scope_sink_provider_to_clause(fw, q, c),
          scope="cross-clause", writes=frozenset({"args.client"}),
          reads=frozenset({"clause", "query"}),
          rationale="imposta client esplicito sul sink clause-scoped (no bleed dalla query). NB (PROV.3): valore dal TESTO della clausola, non sussumibile da runtime-resolve",
          adr="0136"),
    Guard("ensure_health_arg",
          lambda fw, i, q, c: _ensure_health_arg(fw, q, c),
          scope="per-clause", writes=frozenset({"args.include_health"}),
          reads=frozenset({"query"}),
          rationale="§7.9 (turn b66ec6f3): query hardware/status (lessici status/section_focus+machine) con get_processes senza include_health → forzato true. Additivo: aggiunge dati, il focus per-sezione seleziona",
          adr="0177"),
    Guard("route_folder_size",
          lambda fw, i, q, c: _route_folder_size(fw, q, c),
          scope="routing", writes=frozenset({"step.tool", "step", "args.recursive", "args.key"}),
          reads=frozenset({"query", "catalog"}),
          rationale="§7.9 size-misroute (turn 5cdf80d0): «quanto è grande la cartella X» = peso dei file ricorsivi. (A) strutturale: compute(sum,size)←find_dirs → find_files(recursive)+key→size. (B) intento (concept fs.size_query, lessico IT+EN): find_dirs terminale da solo → find_files(recursive)+compute(sum,size). Peso cartella ≠ conteggio sottodir",
          adr="0177"),
    Guard("degenerate_find_to_list",
          lambda fw, i, q, c: _degenerate_find_to_list(fw, i, c),
          scope="routing", writes=frozenset({"step.tool", "args.*"}),
          reads=frozenset({"query", "catalog"}),
          rationale="find_files(base_path) senza selettore → list_dirs (tool-choice, non args)",
          adr="0177"),
    Guard("normalize_document_report_pipeline",
          _normalize_document_report_pipeline,
          v3_only=True, scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"intent.actions", "query", "catalog"}),
          rationale="workflow documentale find/extract/spreadsheet/archive -> una dataflow create-only con dedup, audit e output sul target",
          adr="0177"),
    Guard("normalize_multisource_entity_report_pipeline",
          _normalize_multisource_entity_report_pipeline,
          v3_only=True, scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"intent.actions", "query", "catalog"}),
          rationale="workflow con almeno tre sorgenti fra file/mail/calendario/contatti -> estrazioni indipendenti, schema comune, riconciliazione e sink create-only alimentati dallo stesso carrier",
          adr="0177"),
    Guard("normalize_message_event_report_pipeline",
          _normalize_message_event_report_pipeline,
          v3_only=True, scope="cross-clause", writes=frozenset({"step"}),
          reads=frozenset({"intent.actions", "query", "catalog"}),
          rationale="workflow email+calendario -> schema comune, batching bounded, dedup/conflitti e due output create-only",
          adr="0177"),
)


# CP5.4 (ADR 0177 T2/M4, 6/7): contatore per-guard = la METRICA dello spike
# grammar-on-args. Un guard che MUTA il framework «spara» — meno spari con la
# grammar-args = i guard diventano no-op perché l'LLM non produce più l'errore.
# Strumentazione passiva (snapshot pre/post): NON cambia il comportamento dei
# guard. Attiva solo con METNOS_GUARD_FIRE_COUNT=1 (default off, costo zero in
# prod: to_dict per guard è O(steps)).
_GUARD_FIRE_COUNTS: dict = {}


def guard_fire_counts() -> dict:
    """Copia dei conteggi di fire per-guard (per il bench A/B)."""
    return dict(_GUARD_FIRE_COUNTS)


def reset_guard_fire_counts() -> None:
    _GUARD_FIRE_COUNTS.clear()


def _apply_deterministic_structure_guards(framework: Framework, intent,
                                          query: str,
                                          catalog: Optional[list]) -> Framework:
    """Guard DETERMINISTICI di struttura (no LLM, IDEMPOTENTI — proprietà
    misurata: 33/33 piani reali L0+L1+flagship, sweep S3 5/7 + test T4),
    condivisi da L0/L1 (hit cache) e L3 (proposer). L'ordine e i gate v3
    sono il CONTRATTO dichiarato in `GUARD_PIPELINE` (ADR 0177 T3). NON
    include il re-propose LLM dei dropped: quello resta L3-only."""
    import os as _os
    from . import is_v3
    _v3 = is_v3()
    _count = _os.environ.get("METNOS_GUARD_FIRE_COUNT", "0") == "1"
    for _g in GUARD_PIPELINE:
        if _g.v3_only and not _v3:
            continue
        if _count:
            try:
                _before = framework.to_dict()
            except Exception:
                _before = None
            framework = _g.fn(framework, intent, query, catalog)
            try:
                if _before is not None and framework.to_dict() != _before:
                    _GUARD_FIRE_COUNTS[_g.name] = _GUARD_FIRE_COUNTS.get(_g.name, 0) + 1
            except Exception:
                pass
        else:
            framework = _g.fn(framework, intent, query, catalog)
    return framework


def _finalize_framework_for_run(framework: Framework, intent, query: str,
                                catalog: Optional[list],
                                runtime_ctx) -> Framework:
    """Preparazione UNIVERSALE di un piano PRIMA dell'esecuzione (10/7, turn
    1f1eb714): guard deterministici (l'ULTIMA parola su OGNI piano fresco, ADR
    0177 T3) → output_policy → clausola ordinamento → consent gate → mass gate.

    UNICA fonte per L3 E recovery: il challenger della recovery era l'unico
    path che eseguiva un piano SENZA la pipeline guard → un fallimento remoto
    scivolava a `get_location` per una domanda sull'IP (mai riallineato
    all'intent). Un path che esegue un piano senza passare da qui reintroduce
    quella classe di regressione. I guard sono idempotenti (T4): la doppia
    passata sul path normale è un no-op."""
    framework = _apply_deterministic_structure_guards(
        framework, intent, query, catalog)
    # Output-policy deterministica (matrice intent×data_kind → modo, §7.9):
    # il runtime — non il proposer — sceglie il TERMINALE di presentazione.
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
    # get_approval prima del send. Dopo l'ordinamento, prima dell'esecuzione.
    framework = _insert_consent_gate_if_scheduled(framework, query, runtime_ctx)
    # mass-mutation gate (6/7): delete/move di massa → conferma umana.
    framework = _insert_mass_mutation_gate(framework, query, runtime_ctx)
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


# Verbi fs DISTRUTTIVI/RILOCANTI (rimuovono o spostano dati esistenti): una
# operazione di massa su questi merita conferma umana. write/create sono
# additivi → fuori (primo taglio, bug live 1ba8e2c4 6/7 era una delete).
_MASS_MUTATION_VERBS = ("delete", "move")
_MASS_ACTION_KEY = {"delete": "MSG_ACTION_DELETE", "move": "MSG_ACTION_MOVE"}


def _mass_mutation_threshold() -> int:
    """Soglia oltre cui un'op distruttiva di massa chiede conferma. Env
    METNOS_MASS_MUTATION_THRESHOLD (default 20). ≤0 = gate DISATTIVATO."""
    try:
        return int(os.environ.get("METNOS_MASS_MUTATION_THRESHOLD", "20"))
    except (TypeError, ValueError):
        return 20


def _glob_paths_to_find_files(paths) -> Optional[dict]:
    """Se `paths` (lista inline di un delete/move) contiene GLOB con un UNICO
    parent, ritorna gli args find_files che li materializza (base_path +
    patterns, non ricorsivo = semantica `/dir/*`). None se: nessun glob (il
    conteggio è già len(paths)), parent multipli, o parent glob-ato. OS-agnostico
    (ntpath/posixpath) per i path di device Windows. §7.9 string-ops, niente stat
    (il path può essere remoto)."""
    if not isinstance(paths, list) or not paths:
        return None
    if not any(isinstance(p, str) and any(c in p for c in "*?") for p in paths):
        return None  # nessun glob → len(paths) è già il conteggio vero
    import ntpath
    import posixpath
    parents, bases = set(), []
    for p in paths:
        if not isinstance(p, str) or not p:
            return None
        mod = ntpath if ("\\" in p or (len(p) > 1 and p[1] == ":")) else posixpath
        parent, base = mod.dirname(p), mod.basename(p)
        if not parent or not base or any(c in parent for c in "*?"):
            return None
        parents.add(parent)
        bases.append(base)
    if len(parents) != 1:
        return None
    return {"base_path": next(iter(parents)),
            "patterns": sorted(set(bases)), "recursive": False}


def _insert_mass_mutation_gate(framework, query: str, runtime_ctx):
    """§2.11/§7.9 (bug live 1ba8e2c4, 6/7: recovery ha espanso una dir in 681
    delete remote senza conferma → 195 file persi). Inserisce un `get_approval`
    PRIMA della prima azione DISTRUTTIVA di massa (delete_/move_) con
    `guard_count`+`guard_threshold`: il gate CHIEDE solo se gli item superano la
    soglia, altrimenti passa trasparente (una delete di 3 file non disturba).

    Universale §7.9: inserito dal RUNTIME (deterministico) DOPO il coerce (i suoi
    arg guard_* non sono soggetti a Guard #0). Copre sia i piani normali sia
    quelli RICOSTRUITI dalla recovery (chiamato in entrambi i path). La pausa+
    ripresa riusa il gate-resume esistente (on-approve la pipeline si riesegue
    col gate auto-passato). Skip: ripresa post-approvazione, gate già presente,
    soglia disattivata, nessuna azione di massa o conteggio sotto soglia."""
    try:
        if (runtime_ctx or {}).get("_gate_approved"):
            return framework
        thr = _mass_mutation_threshold()
        if thr <= 0:
            return framework
        # Turni SCHEDULATI: azione INTENZIONALE pre-autorizzata da chi l'ha
        # pianificata, e nessun umano può approvare nell'istante → gate SKIP
        # (altrimenti la manutenzione notturna resta appesa). Specularmente al
        # consent-gate outbound, che invece è schedulato-ONLY. La protezione
        # mira alla delete INTERATTIVA a sorpresa (bug 1ba8e2c4).
        try:
            from treated_issues_guard import is_scheduled_turn
            if is_scheduled_turn():
                return framework
        except Exception:
            pass
        steps = list(getattr(framework, "steps", None) or [])
        if any((s.tool or "") == "get_approval" for s in steps):
            return framework
        from messages import get as _msg_get
        from .types import StepSpec
        rc = runtime_ctx or {}
        where = rc.get("target_device") or _msg_get("MSG_LOCAL_HERE")
        for idx, s in enumerate(steps):
            verb = (s.tool or "").split("_", 1)[0]
            if verb not in _MASS_MUTATION_VERBS:
                continue
            a = s.args if isinstance(s.args, dict) else {}
            fs = a.get("from_step")
            prefix = list(steps[:idx])   # step immutati prima dell'azione
            mut_step = s                 # lo step d'azione (eventualmente riscritto)
            # (1) consumer di un produttore (from_step): conteggio NOTO solo a
            #     runtime → guard_count=${stepN.@count}.
            # (2) inline paths con GLOB (`*?`): il conteggio VERO è post-espansione
            #     (backend/device), invisibile a plan-time (bug e2e 6/7:
            #     delete(["/dir/*"]) len=1 sfuggiva). Riscrivi a
            #     [find_files(dir,pattern), delete(from_step)] così @count lo
            #     materializza a runtime, locale E su device.
            # (3) lista inline SENZA glob: conteggio noto ORA → gate solo se >soglia.
            if isinstance(fs, int) and 1 <= fs <= idx:
                guard_count = f"${{step{fs}.@count}}"
                n_display = guard_count
            else:
                paths = a.get("paths")
                ff_args = (_glob_paths_to_find_files(paths)
                           if isinstance(paths, list) else None)
                if ff_args is not None:
                    # riscrittura glob → find_files precursore + delete(from_step)
                    ff_idx = len(prefix) + 1
                    consumer_args = {k: v for k, v in a.items()
                                     if k != "paths" and not k.startswith("_")}
                    consumer_args["from_step"] = ff_idx
                    prefix = prefix + [StepSpec(tool="find_files", args=ff_args)]
                    mut_step = StepSpec(tool=s.tool, args=consumer_args)
                    guard_count = f"${{step{ff_idx}.@count}}"
                    n_display = guard_count
                else:
                    n_inline = len(paths) if isinstance(paths, list) else None
                    if n_inline is None:
                        v = a.get("entries")
                        n_inline = len(v) if isinstance(v, list) else None
                    if n_inline is None or n_inline <= thr:
                        continue  # non contabile, o sotto soglia
                    guard_count, n_display = n_inline, n_inline
            action = _msg_get(_MASS_ACTION_KEY.get(verb, "MSG_ACTION_DELETE"))
            prompt = _msg_get("MSG_CONSENT_GATE_MASS_MUTATION",
                              action=action, n=n_display, where=where)
            gate = StepSpec(tool="get_approval", args={
                "prompt": prompt,
                "on_approve": {"tool": "final_answer", "args": {}},
                "guard_count": guard_count,
                "guard_threshold": thr,
                "timeout_s": 3600,
                "channel": rc.get("channel") or "",
                "actor": rc.get("actor") or "",
            })
            # RINUMERA i from_step degli step IN CODA (bug latente esposto 7/7
            # dal guard scope_dirs, che appende find_dirs→delete_dirs(from_step)
            # DOPO la prima delete): inserire il gate (e l'eventuale find_files
            # precursore del glob) sposta le posizioni → un consumer a valle che
            # puntava alla mutazione o a un altro step di coda deve slittare, o
            # consumerebbe il gate (0 item → azione muta, over/under-deletion).
            # Shift = passi inseriti prima della coda; i ref al PREFISSO
            # immutato (<= idx, 1-based) restano. Stessa logica di
            # _ensure_extract_clause.
            shift = len(prefix) + 1 - idx  # glob: 2 (find_files+gate); altrimenti 1
            tail = list(steps[idx + 1:])
            if shift:
                for _ts in tail:
                    _fa = _ts.args if isinstance(_ts.args, dict) else None
                    _q = _fa.get("from_step") if _fa else None
                    if isinstance(_q, int) and _q >= idx + 1:
                        _fa["from_step"] = _q + shift
            framework.steps = prefix + [gate, mut_step] + tail
            log.info("[mass_mutation_gate] get_approval inserito prima di %s "
                     "(soglia %d, coda rinumerata +%d)", s.tool, thr, shift)
            return framework
        return framework
    except Exception as ex:  # noqa: BLE001 — best-effort, non blocca il turno
        log.warning("[mass_mutation_gate] noop: %r", ex)
        return framework


def _inject_gate_resume_if_paused(run, query: str, runtime_ctx,
                                  framework: Framework | None = None) -> None:
    """gate-resume (20/6/2026): se un gate get_approval ha messo in PAUSA la
    pipeline (run.gate_dialog_id), sovrascrive l'on_complete del dialog
    (gate_dispatch → resume_engine_gate) col contesto di ripresa. On-approve il
    callback riesegue il turno con `pre_approved_gate` → il gate auto-passa e
    gli step a valle (send/write) girano. §7.9 deterministico. No-op se nessun
    gate in pausa. Universale: vale per ogni compound con un gate di consenso."""
    # Anche un input raccolto dentro un executor puo' sospendere una pipeline
    # stateful (per esempio un codice monouso nel browser). Se il callback
    # rilancia lo stesso executor, conserva la coda sul result osservato invece
    # di rieseguire l'intera query e perdere sessione/cursor/handle.
    if run and framework is not None:
        paused_input = next((
            step for step in reversed(getattr(run, "steps", []) or [])
            if isinstance(getattr(step, "result", None), dict)
            and step.result.get("decision") == "needs_inputs"
        ), None)
        if paused_input is not None:
            payload = paused_input.result.get("needs_inputs") or {}
            callback = payload.get("on_complete") or {}
            paused_tool = str(getattr(paused_input, "tool", "") or "")
            if (isinstance(callback, dict)
                    and callback.get("type") == "resume_executor_with_values"
                    and callback.get("executor") == paused_tool):
                paused_idx = int(getattr(paused_input, "step_idx", 0) or 0)
                history_pos = next(
                    (pos for pos, item in enumerate(run.steps, start=1)
                     if item is paused_input), paused_idx)
                max_history_pos = (
                    history_pos + len(framework.steps) - paused_idx)
                idx_map = {old: old - history_pos + 1
                           for old in range(history_pos,
                                            max_history_pos + 1)}
                tail_steps = [{
                    "tool": step.tool,
                    "args": _remap_step_refs(step.args or {}, idx_map),
                    **({"if_prev_entries_nonempty": True}
                       if step.if_prev_entries_nonempty else {}),
                } for step in framework.steps[paused_idx:]]
                if tail_steps:
                    callback.update({
                        "type": "resume_executor_values_tail",
                        "tail_steps": tail_steps,
                        "tail_final_message": _remap_step_refs(
                            framework.final_message or "", idx_map),
                        "original_query": (
                            (runtime_ctx or {}).get("user_query_raw") or query),
                        "conversation_id": (
                            (runtime_ctx or {}).get("conversation_id") or ""),
                    })
                    payload["on_complete"] = callback
                    log.info("[input_resume] coda preservata per %s (%d step)",
                             paused_tool, len(tail_steps))

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
        paused = next((s for s in reversed(getattr(run, "steps", []) or [])
                       if getattr(s, "tool", "") and
                       getattr(s, "step_idx", 0)), None)
        paused_tool = getattr(paused, "tool", "") if paused else ""

        # Un executor auto-riprendibile mette nel branch approvato se stesso
        # con lo stato opaco necessario (token broker, cursor, handle, ecc.).
        # Riconoscilo dal contratto, non dal nome: rilanciare l'intera query
        # perderebbe lo stato osservato e potrebbe ricreare lo stesso gate.
        approve_branch = oc.get("on_approve") or {}
        approve_tool = (
            (approve_branch.get("tool") or approve_branch.get("executor"))
            if isinstance(approve_branch, dict) else ""
        )
        if (paused_tool and approve_tool == paused_tool
                and oc.get("type") == "gate_dispatch" and framework is not None):
            paused_idx = int(getattr(paused, "step_idx", 0) or 0)
            # `paused_idx` e' relativo al framework corrente, mentre i suoi
            # from_step/${stepN} puntano alla history completa, che nelle
            # continuazioni contiene gia' il result approvato come seed. Usa la
            # posizione reale nella history: dopo il nuovo consenso quel result
            # diventera' il seed 1 e tutta la coda traslera' di conseguenza.
            history_pos = next(
                (pos for pos, item in enumerate(run.steps, start=1)
                 if item is paused), paused_idx)
            max_history_pos = history_pos + len(framework.steps) - paused_idx
            idx_map = {old: old - history_pos + 1
                       for old in range(history_pos, max_history_pos + 1)}
            tail_steps = []
            for step in framework.steps[paused_idx:]:
                tail_steps.append({
                    "tool": step.tool,
                    "args": _remap_step_refs(step.args or {}, idx_map),
                    **({"if_prev_entries_nonempty": True}
                       if step.if_prev_entries_nonempty else {}),
                })
            st["on_complete"] = {
                "type": "resume_executor_gate_tail",
                "gate_approve_value": oc.get("approve_value", "approve"),
                "gate_on_approve": oc.get("on_approve"),
                "gate_on_reject": oc.get("on_reject"),
                "tail_steps": tail_steps,
                "tail_final_message": _remap_step_refs(
                    framework.final_message or "", idx_map),
                "original_query": rc.get("user_query_raw") or query,
                "conversation_id": rc.get("conversation_id") or "",
            }
            _dp.save_pending(sender, did, st)
            log.info("[gate_resume] dialog %s → executor-tail (%s, %d step)",
                     did, paused_tool, len(tail_steps))
            return

        st["on_complete"] = {
            "type": "resume_engine_gate",
            # Query RAW dell'utente (CON la destinazione «su pc-X»): la query
            # di dispatch è già strippata dell'adjunct — rilanciarla farebbe
            # dipendere l'host di esecuzione dallo sticky target (bug live
            # 981ddc9f 6/7: fragile e potenzialmente cross-host).
            "original_query": rc.get("user_query_raw") or query,
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
              submit_executor_cb: Optional[Callable] = None,
              can_parallelize_cb: Optional[Callable] = None,
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
        submit_executor=submit_executor_cb,
        can_parallelize=can_parallelize_cb,
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
            # VALIDITÀ DEL MONDO a hit-time (ADR 0182, sussume la morte C1):
            # il piano è servibile SOLO se le firme registrate combaciano col
            # mondo corrente — tool referenziati con lo STESSO digest (§7.10:
            # re-sign post-edit ⇒ mismatch; sparito ⇒ `!missing`) E famiglie di
            # candidati invariate per l'intent (capacità nuova ⇒ la decisione
            # va ripresa). Mismatch/sig-vuota → delete + fall-through a L1/L3;
            # il successo ri-registra col piano e la firma freschi.
            from .cache_validity import validate as _cv_validate
            _ok, _why = _cv_validate(fp_hit.tools_sig, fp_hit.pool_sig,
                                     fp_hit.framework, intent, catalog)
            if not _ok:
                log.info("[L0 fastpath] fp_id=%d INVALIDATO: %s → morte + "
                         "fall-through", fp_hit.fp_id, _why)
                _fp.delete(fp_hit.fp_id)
                fp_hit = None
        # GARANZIA (Roberto 15/6): mai eseguire un piano L0 con step mutante i
        # cui valori-arg discriminanti non sono nella query corrente (re-plan).
        _fp_ungrounded = (_ungrounded_mutating_args(fp_hit.framework, query)
                          if fp_hit is not None else [])
        if fp_hit is not None and _fp_ungrounded:
            log.info("[L0 fastpath] REJECT mis-serve: step mutante con valore "
                     "non presente nella query → fall-through/re-plan "
                     "evidence=%s", _fp_ungrounded[:5])
            fp_hit = None
        if fp_hit is not None:
            if verbose:
                log.info("[L0 fastpath] hit (%s, sim=%.2f): %s",
                          fp_hit.match_kind, fp_hit.similarity,
                          fp_hit.canonical_text)
            # L0 usa la STESSA finalizzazione di L3/recovery: guard, policy di
            # output, ordinamento e gate. La vecchia sequenza duplicata non
            # applicava output_policy, quindi un framework cached con
            # `get_now -> @count` continuava a rispondere “Totale: 0”.
            fp_hit.framework = _finalize_framework_for_run(
                fp_hit.framework, intent, query, catalog, runtime_ctx)
            run = executor.run(fp_hit.framework, query=query,
                                runtime_ctx=runtime_ctx,
                                remediate_args_cb=remediate_args_cb,
                                progress=progress)
            # Self-healing su ERRORE (1/7/2026, §2.8): un piano cachato che ora
            # FALLISCE (drift ambiente/schema) non va ri-servito come errore
            # secco a ogni ripetizione — l'hit rinfresca last_used (l'aging non
            # lo pota mai) e L0 vince in cascata (la query non raggiunge più il
            # piano pieno). Delete (economia: si ricrea al prossimo successo) +
            # fall-through a L1/L3, che ripianificano con recovery.
            if run.final_kind == "error":
                _fp.delete(fp_hit.fp_id)
                # Anti-doppia-esecuzione (2/7/2026, §2.8/§2.9): se il leg
                # fallito ha GIÀ committato ≥1 side-effect (send ok, poi move
                # fallisce), il fall-through L3 ri-eseguirebbe TUTTO il
                # framework → mail/evento DUPLICATO. Errore onesto; la delete
                # sopra resta (al prossimo turno si ri-pianifica da zero).
                _committed = _leg_committed_mutations(run)
                if _committed:
                    log.warning("[L0 fastpath] fp_id=%d in ERRORE ma side-effect"
                                " già committati %s → NIENTE fall-through "
                                "(errore onesto, anti-doppia-esecuzione)",
                                fp_hit.fp_id, _committed)
                    return DispatchResult(
                        final_text=run.final_text, final_kind=run.final_kind,
                        match_source="fastpath",
                        framework_hash=run.framework_hash,
                        elapsed_ms=int((time.time() - t_start) * 1000),
                        run=run, framework=fp_hit.framework)
                # Osservabilità (§2.8, 10/7 turn 1f1eb714): il TurnLog registra
                # solo l'ULTIMO run — l'errore del run L0 era invisibile.
                _last = (run.steps or [])[-1] if run.steps else None
                _lerr = (getattr(_last, "result", None) or {}).get("error") \
                    if _last and isinstance(getattr(_last, "result", None), dict) else None
                log.info("[L0 fastpath] fp_id=%d esegue in ERRORE → morte + "
                         "fall-through a L1/L3 (re-plan) — aborted=%s "
                         "last_step=%s err=%.200s", fp_hit.fp_id,
                         run.aborted_reason,
                         getattr(_last, "tool", None), str(_lerr))
            else:
                _inject_gate_resume_if_paused(
                    run, query, runtime_ctx, framework=fp_hit.framework)
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
        if ap_hit is not None:
            # VALIDITÀ DEL MONDO a hit-time (ADR 0182, gemello di L0): firme
            # registrate alla promozione vs mondo corrente. Mismatch/sig-vuota
            # → fall-through a L3 (l'autopath NON viene cancellato qui: la
            # promozione è capitale di feedback umano — il reaper C3 pota le
            # righe la cui sig resta stantia; il primo turno L3 riuscito
            # ri-osserva e la ri-promozione segue il flusso normale).
            from .cache_validity import validate as _cv_validate
            _ok, _why = _cv_validate(ap_hit.tools_sig, ap_hit.pool_sig,
                                     ap_hit.framework, intent, catalog)
            if not _ok:
                log.info("[L1 autopath] %s INVALIDATO: %s → fall-through L3",
                         ap_hit.autopath_id, _why)
                ap_hit = None
        # GARANZIA (Roberto 15/6): stessa invariante di L0 — un piano L1 con step
        # mutante i cui valori-arg non sono nella query corrente NON va eseguito
        # (un autopath con valore baked servirebbe il target sbagliato).
        _ap_ungrounded = (_ungrounded_mutating_args(ap_hit.framework, query)
                          if ap_hit is not None else [])
        if ap_hit is not None and _ap_ungrounded:
            log.info("[L1 autopath] REJECT mis-serve: step mutante con valore "
                     "non presente nella query → fall-through a L3 (re-plan) "
                     "evidence=%s", _ap_ungrounded[:5])
            ap_hit = None
        if ap_hit is not None:
            if verbose:
                log.info("[L1 autopath] hit autopath=%s uses=%d", ap_hit.autopath_id, ap_hit.uses)
            # L1, come L0, passa dall'unica pipeline pre-esecuzione. Evita
            # drift fra piani cached e piani freschi su presentazione e gate.
            ap_hit.framework = _finalize_framework_for_run(
                ap_hit.framework, intent, query, catalog, runtime_ctx)
            run = executor.run(ap_hit.framework, query=query,
                                runtime_ctx=runtime_ctx,
                                remediate_args_cb=remediate_args_cb,
                                progress=progress)
            # Self-healing su ERRORE (1/7/2026, gemello di L0): il champion che
            # ora fallisce NON viene ritornato secco — fall-through a L3
            # (proposer+recovery). Niente delete qui: il demote del champion
            # resta governato dal feedback (anti_autopath, 3+ fail). Niente
            # observation dell'esecuzione fallita: la registra il leg L3.
            if run.final_kind == "error":
                # Anti-doppia-esecuzione (2/7/2026): stessa guardia di L0 —
                # side-effect già committati nel leg fallito → errore onesto,
                # NIENTE re-plan (L3 duplicherebbe il side-effect).
                _committed = _leg_committed_mutations(run)
                if _committed:
                    log.warning("[L1 autopath] champion %s in ERRORE ma "
                                "side-effect già committati %s → NIENTE "
                                "fall-through (errore onesto)",
                                ap_hit.autopath_id, _committed)
                    return DispatchResult(
                        final_text=run.final_text, final_kind=run.final_kind,
                        match_source="autopath",
                        framework_hash=run.framework_hash,
                        elapsed_ms=int((time.time() - t_start) * 1000),
                        run=run, framework=ap_hit.framework)
                log.info("[L1 autopath] champion %s esegue in ERRORE → "
                         "fall-through a L3 (re-plan)", ap_hit.autopath_id)
            else:
                _inject_gate_resume_if_paused(
                    run, query, runtime_ctx, framework=ap_hit.framework)
                # Record observation per future feedback hooks. Skip se il piano
                # non è cacheabile (single-executor / valore numerico baked dalla
                # query): L1 non deve avere valori baked (Roberto 15/6).
                # + no-cache-on-error (9/7): solo run efficaci.
                if (turn_id and intent.is_complete()
                        and _run_is_cacheworthy(run)
                        and _should_cache_plan(ap_hit.framework, query)):
                    _ap.record_observation(
                        turn_id=turn_id, intent=intent,
                        framework=ap_hit.framework, query=query,
                        latency_ms=run.elapsed_ms, catalog=catalog)
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
                # Le guardie DETERMINISTICHE (§7.9) devono essere l'ULTIMA parola:
                # il re-propose del validator produce un piano FRESCO che le
                # scavalcherebbe (regressione universale, non di un guard
                # specifico). Ri-applicale — sono idempotenti.
                framework = _apply_deterministic_structure_guards(
                    framework2, intent, query, catalog)

    # Preparazione UNIVERSALE pre-esecuzione (funzione condivisa, 10/7):
    # guards (idempotenti — 2ª passata no-op sul path normale) → output_policy
    # → ordering → consent gate → mass gate. UNICA fonte con la recovery.
    framework = _finalize_framework_for_run(framework, intent, query,
                                            catalog, runtime_ctx)

    # Execute
    run = executor.run(framework, query=query,
                        runtime_ctx=runtime_ctx,
                        remediate_args_cb=remediate_args_cb,
                        progress=progress)
    _inject_gate_resume_if_paused(run, query, runtime_ctx, framework=framework)

    # Record observation (per future feedback). Skip se non cacheabile
    # (single-executor / valore numerico baked): L1 non deve avere valori baked.
    # Seed-state (ADR 0177 M1): non cachare i turni con allegati (vedi L0/L1).
    # + no-cache-on-error (9/7): un run in errore/degenere NON diventa
    # observation (prima entrava e contribuiva alle promozioni cluster —
    # una delle 3 modalità di fallimento L0/L1, turn ad19e8b4).
    if (turn_id and intent.is_complete() and not seed_state
            and _run_is_cacheworthy(run)
            and _should_cache_plan(framework, query)):
        try:
            _ap.record_observation(
                turn_id=turn_id, intent=intent, framework=framework,
                query=query, latency_ms=run.elapsed_ms, catalog=catalog)
            # W1 learning-loop (ADR 0185): turno engine OK e COSTOSO ripetuto
            # → autopath SHADOW (senza aspettare il ✓ umano). Deterministico,
            # soglie METNOS_SEED_STEPS/METNOS_SEED_REPEAT; no-op se un
            # autopath active esiste già per l'intent.
            _ap.seed_from_run(
                intent=intent, framework=framework,
                n_steps=len(run.steps or []), catalog=catalog)
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
        # ANTI-DOPPIA-ESECUZIONE al confine recovery (bug live mat-2/anom,
        # 7/7): un leg fallito-PARZIALE ha gia' COMMITTATO mutazioni (delete
        # glob §2.4: 3 file rimossi, 1 system-file refuse → ok=False) — la
        # recovery rilancerebbe una SECONDA pipeline mutante sugli stessi
        # target (leg-1 invisibile nel turn record → «cancellazioni
        # fantasma»). Con mutazioni committate: NIENTE recovery, si passa
        # dritti all'esito parziale onesto qui sotto (SoT del criterio:
        # _leg_committed_mutations / pipeline_effects, 2/7).
        _committed = _leg_committed_mutations(run)
        if _committed:
            log.info("[L3 recovery] SKIP: mutazioni gia' committate nel leg "
                     "fallito %s — esito parziale, no re-run", _committed)
        if not _committed and err_class in (
                "wrong_tool", "wrong_args", "missing_input"):
            if verbose:
                log.info("[L3 recovery] class=%s", err_class)
            framework_alt = recovery.recover(
                failed_run=run, query=query, intent=intent,
                pool=pool_names, proposer=proposer,
                llm_call=llm_call_wise, lang=lang, catalog=catalog)
            if framework_alt is not None:
                # Preparazione UNIVERSALE (10/7, turn 1f1eb714): il piano di
                # recovery riceve la STESSA pipeline del path L3 — prima aveva
                # solo ordering+mass-gate, SENZA i guard: il challenger poteva
                # eseguire un tool semanticamente estraneo all'intent
                # (get_location per «ip …» dopo 2 fallimenti remoti).
                framework_alt = _finalize_framework_for_run(
                    framework_alt, intent, query, catalog, runtime_ctx)
                run2 = executor.run(framework_alt, query=query,
                                     runtime_ctx=runtime_ctx,
                                     remediate_args_cb=remediate_args_cb,
                                progress=progress)
                # Gate in PAUSA (final_kind='ask' + gate_dialog_id): la pipeline
                # attende il consenso umano → cabla il resume e ritorna la
                # richiesta d'input, NON un errore. §2.11.
                if getattr(run2, "gate_dialog_id", ""):
                    _inject_gate_resume_if_paused(
                        run2, query, runtime_ctx, framework=framework_alt)
                    return DispatchResult(
                        final_text=run2.final_text, final_kind=run2.final_kind,
                        match_source="recovery",
                        framework_hash=run2.framework_hash,
                        elapsed_ms=int((time.time() - t_start) * 1000),
                        run=run2, framework=framework_alt,
                        error_class=err_class)
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
                # §2.8 (bug live 1ba8e2c4, 6/7): il run di RECOVERY è fallito.
                # La verità più recente è run2 — che può aver TENTATO una
                # mutazione (es. delete di massa → timeout con esecuzione
                # PARZIALE sul device). Spiegare run1 la nasconderebbe: da qui
                # in poi terminator/TurnLog vedono run2.
                run = run2
                framework = framework_alt
                err_class = classify_error(run2)
        # §2.8 esito PARZIALE (bug live 8025922, 6/7): un run «error» il cui
        # step MUTANTE ha comunque effetti reali (delete 456: 455 ok, 1
        # system-file rifiutato) NON è materia da terminator («pipeline
        # malformata» = falso). final vuoto+kind answer → il finalizer
        # TurnLog compone la verità (conteggio + notice sul fallito).
        _pm = 0
        for _s in reversed(run.steps or []):
            _r = _s.result if isinstance(_s.result, dict) else {}
            if ((_s.tool or "").split("_", 1)[0] in
                    ("delete", "move", "write", "create", "send", "share",
                     "order", "change")
                    and (_r.get("ok_count") or _r.get("results"))):
                _pm = _r.get("ok_count") or len(_r.get("results") or [])
                break
        if _pm:
            from messages import get as _pmsg
            if str(run.aborted_reason or "").startswith("cap_steps"):
                # Il cap dopo una mutazione create-only non è un
                # completamento. Il vecchio ramo partial_mutation occultava
                # gli artefatti a valle mai eseguiti.
                _collected = 0
                for _s in reversed(run.steps or []):
                    _r = _s.result if isinstance(_s.result, dict) else {}
                    if isinstance(_r.get("entries"), list):
                        _collected = len(_r["entries"])
                        break
                return DispatchResult(
                    final_text=_pmsg(
                        "MSG_SEARCH_PARTIAL_OR_INTERRUPTED", n=_collected),
                    final_kind="answer",
                    match_source="partial_interrupted",
                    framework_hash=run.framework_hash,
                    elapsed_ms=int((time.time() - t_start) * 1000),
                    run=run, framework=framework, error_class=err_class)
            return DispatchResult(
                final_text=_pmsg("MSG_DEGENERATE_FINAL_MUTATIONS", n=_pm),
                final_kind="answer", match_source="partial_mutation",
                framework_hash=run.framework_hash,
                elapsed_ms=int((time.time() - t_start) * 1000),
                run=run, framework=framework, error_class=err_class)
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
