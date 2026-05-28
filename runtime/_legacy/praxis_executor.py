"""Praxis executor — Noûs (νοῦς), l'intelletto puro che esegue.

Nella triade Praxis Engine (ADR 0161):
  Mētis (praxis_propose)  propone il piano in 1 call.
  Noûs (questo modulo)    esegue deterministico, senza LLM nel loop.
  Praxis (praxis.py)      ricorda cio' che ha funzionato.

Noûs riceve il framework da Mētis e lo realizza. Mai esita, mai inventa,
mai si interroga. Solo data routing: from_step piping, ${FILLER} resolve,
${stepN.field} expand, invoke_executor, vaglio.judge, accumulate.

E' la mente che agisce. Niente saggezza qui — quella e' altrove.

Praxis executor — runs a framework deterministically.

Input: framework dict {steps, fillers, final_message}.
Output: PraxisRun (steps eseguiti + final_text + ok_count + framework_hash).

Flow:
  for step in framework["steps"]:
    1. Risolvi from_step: <N> → estrai entries da steps_history[N-1].
    2. Risolvi ${FILLER:name} → LLM fast tier o default.
    3. Risolvi ${stepN.field} → estrai field da steps_history[N-1].
    4. validate_args (requires_one_of, forbid_placeholder_values).
    5. invoke_executor(tool, args) → result.
    6. vaglio.judge(result) → safe? else stop.
    7. accumulate steps_history.
  Render final_message template.
  Return PraxisRun.

Filosofia §7.9: orchestrator DETERMINISTICO. LLM solo per fillers.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


_FILLER_RE = re.compile(r"\$\{FILLER:([a-zA-Z_][a-zA-Z0-9_]*)\}")
# Step reference: supporta dot-path nested (es. ${step1.health.thermal})
_STEPREF_RE = re.compile(r"\$\{step(\d+)\.(@?[a-zA-Z_][a-zA-Z0-9_.*]*)\}")
# Runtime placeholder (ADR 0163, 26/5/2026): ${RUNTIME:key} risolto al
# turno corrente. Whitelist chiusa di chiavi sotto in _RUNTIME_RESOLVERS.
_RUNTIME_RE = re.compile(r"\$\{RUNTIME:([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _build_runtime_resolvers(ctx: dict) -> dict[str, str]:
    """Whitelist deterministica §7.9 dei valori runtime esponibili come
    placeholder ${RUNTIME:key}. ctx contiene actor/channel/lang/conversation_id
    passati da agent_runtime al boot del Praxis.execute_framework.

    Actor "host"/"guest" label generico → risolto al display_name reale via
    users.db (read-only). Se users.db assente o nessun match → label
    invariato. Determinismo §7.9, no LLM.
    """
    actor = str(ctx.get("actor") or "")
    if actor.lower() in ("host", "guest"):
        try:
            import sqlite3
            import config as _C
            udb = _C.PATH_USER_DATA / "users.db"
            if udb.exists():
                conn = sqlite3.connect(str(udb))
                row = conn.execute(
                    "SELECT name, display_name FROM users WHERE role=? LIMIT 1",
                    (actor.lower(),)).fetchone()
                conn.close()
                if row:
                    actor = row[1] or row[0] or actor
        except Exception:
            pass
    return {
        "actor": actor,
        "lang": str(ctx.get("lang") or "it"),
        "channel": str(ctx.get("channel") or ""),
    }


def _resolve_runtime_placeholders(args: dict, runtime_ctx: dict) -> dict:
    """Sostituisce ${RUNTIME:key} string-encoded con valore corrente +
    inietta `_actor`/`_lang`/`_channel` come arg "hidden" (prefix `_` =
    runtime-injected, mai emesso da LLM, esecutori liberi di ignorarli).

    Pattern parallelo a _resolve_fillers: walka args (scalari + liste),
    sostituisce solo se chiave nella whitelist. Chiave unknown → resta
    letterale (no crash, no silent default).
    """
    if not runtime_ctx:
        return args
    resolvers = _build_runtime_resolvers(runtime_ctx)
    def _sub_one(v):
        if not isinstance(v, str):
            return v
        m = _RUNTIME_RE.search(v)
        if not m:
            return v
        def _repl(mm):
            key = mm.group(1)
            return resolvers.get(key, mm.group(0))
        return _RUNTIME_RE.sub(_repl, v)
    out: dict = {}
    for k, v in args.items():
        if isinstance(v, list):
            out[k] = [_sub_one(x) for x in v]
        else:
            out[k] = _sub_one(v)
    # Inietta runtime context come arg `_*` (prefix riservato).
    for ck, cv in resolvers.items():
        if cv:
            out.setdefault(f"_{ck}", cv)
    return out


def _resolve_dotted(obj, path: str):
    """Traverse dict/list per dot-path. Es:
      - obj={'health':{'thermal':45}}, path='health.thermal' → 45
      - obj={'entries':[{'name':'X'}]}, path='entries.0.name' → 'X'
      - obj={'entries':[{'examples':[{'image_path':'/a.jpg'}]}]},
        path='entries.0.examples.*.image_path' →
        ['/a.jpg', ...] (PROJECTION map: `*` proietta su tutti gli elementi
        della list, ritorna list con field estratto per ognuno).
    Parte numerica intera = index list; `*` = projection map list;
    altrimenti dict key.
    """
    cur = obj
    parts = path.split(".")
    for i, part in enumerate(parts):
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        elif isinstance(cur, list) and part == "*":
            # Projection: applica i sotto-path rimanenti a ogni elemento.
            rest = ".".join(parts[i + 1:])
            if not rest:
                return list(cur)  # `*` finale = lista come-è
            return [v for v in (_resolve_dotted(el, rest) for el in cur)
                    if v is not None]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


@dataclass
class StepRun:
    step_idx: int
    tool: str
    args: dict
    result: dict
    ok: bool
    latency_ms: int


@dataclass
class PraxisRun:
    steps: list[StepRun] = field(default_factory=list)
    final_text: str = ""
    final_kind: str = "answer"
    ok_count: int = 0
    elapsed_ms: int = 0
    framework_hash: str = ""
    aborted_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "steps": [
                {"idx": s.step_idx, "tool": s.tool, "args": s.args,
                  "result": s.result, "ok": s.ok, "latency_ms": s.latency_ms}
                for s in self.steps
            ],
            "final_text": self.final_text,
            "final_kind": self.final_kind,
            "ok_count": self.ok_count,
            "elapsed_ms": self.elapsed_ms,
            "framework_hash": self.framework_hash,
            "aborted_reason": self.aborted_reason,
        }


# ── Resolution helpers ──────────────────────────────────────────────────

def _find_list_of_dicts(result: dict) -> list:
    """Pattern strutturale §7.3: ritorna prima list di dict trovata nel
    result (esclude entries/results gia' provati). No nomi hardcoded:
    scoperta per TIPO del valore (list[dict]).
    """
    for key, val in (result or {}).items():
        if key in ("entries", "results"):
            continue
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val
    return []


def _resolve_from_step(args: dict, history: list[StepRun]) -> dict:
    """Espande from_step: N → entries dello step N (1-based).

    Cerca `entries`, poi `results`, poi pattern strutturale (prima list[dict]
    nel result). Universale §7.3, nessun nome di campo hardcoded.
    """
    if "from_step" not in args:
        return args
    n = args.get("from_step")
    if not isinstance(n, int) or n < 1 or n > len(history):
        return args
    src = history[n - 1].result
    entries = (src.get("entries")
                or src.get("results")
                or _find_list_of_dicts(src)
                or [])
    out = {k: v for k, v in args.items() if k != "from_step"}
    out["entries"] = entries
    return out


def _resolve_fillers(args: dict, fillers: dict, *,
                      llm_call_fast: Optional[Callable] = None,
                      query_context: str = "",
                      intent_hash: str = "") -> dict:
    """Sostituisce ${FILLER:name} con valore risolto (cache | LLM fast | default)."""
    from praxis_propose import resolve_filler
    out: dict = {}
    for k, v in args.items():
        if isinstance(v, str):
            m = _FILLER_RE.search(v)
            if m:
                name = m.group(1)
                spec = (fillers or {}).get(name, {})
                resolved = resolve_filler(name, spec,
                                            llm_call=llm_call_fast,
                                            query_context=query_context,
                                            intent_hash=intent_hash)
                out[k] = _FILLER_RE.sub(resolved, v)
                continue
        if isinstance(v, list):
            out[k] = [_resolve_fillers_scalar(x, fillers,
                                               llm_call_fast=llm_call_fast,
                                               query_context=query_context,
                                               intent_hash=intent_hash)
                       for x in v]
            continue
        out[k] = v
    return out


def _resolve_fillers_scalar(v: Any, fillers: dict, *,
                              llm_call_fast: Optional[Callable] = None,
                              query_context: str = "",
                              intent_hash: str = "") -> Any:
    if isinstance(v, str) and _FILLER_RE.search(v):
        from praxis_propose import resolve_filler
        m = _FILLER_RE.search(v)
        name = m.group(1)
        spec = (fillers or {}).get(name, {})
        return _FILLER_RE.sub(
            resolve_filler(name, spec, llm_call=llm_call_fast,
                            query_context=query_context,
                            intent_hash=intent_hash),
            v,
        )
    return v


def _resolve_stepref(value: Any, history: list[StepRun]) -> Any:
    """Sostituisce ${stepN.field} → valore dal result di step N.
    Supporta dot-path nested (es. ${step1.health.thermal}) e projection
    (es. ${step1.entries.0.examples.*.image_path} → list).

    Full-match (placeholder = intero valore) preserva il TIPO originale
    (list/dict/int rimangono tali). Embedded-match (placeholder in mezzo a
    stringa più ampia) stringifica il valore. Cruciale per pipeline:
    `paths=${step1.entries.0.examples.*.image_path}` deve risolvere a
    list di paths, non a string.
    """
    if not isinstance(value, str):
        return value
    # Full-match: placeholder è l'intero valore → ritorna tipo nativo.
    fm = _STEPREF_RE.fullmatch(value.strip())
    if fm:
        n = int(fm.group(1))
        path = fm.group(2)
        if 1 <= n <= len(history):
            val = _resolve_dotted(history[n - 1].result, path)
            return val if val is not None else value
        return value
    # Embedded: stringify e sostituisci nel testo.
    def _sub(m):
        n = int(m.group(1))
        path = m.group(2)
        if 1 <= n <= len(history):
            val = _resolve_dotted(history[n - 1].result, path)
            return "" if val is None else str(val)
        return ""
    return _STEPREF_RE.sub(_sub, value)


def _render_final_message(template: str, history: list[StepRun]) -> str:
    """Risolve ${stepN.path}. Magic resolver pattern §7.3:
    - @count    → cascata available_total → ok_count → used → len(first list)
    - @fmt:X.Y  → dot-path verso X.Y; se Y e' dict, formatta come "k v · k v"
    - .a.b.c    → dot-path. Se valore finale e' dict (non scalar), formatta
                   ricorsivamente come dict compatto "k=v · k=v" (mai dict
                   nudo nel template finale).
    """
    if not template:
        return ""
    def _format_value(v) -> str:
        if v is None:
            return ""
        if isinstance(v, dict):
            # Formato compatto k=v · k=v (esclude available/_meta keys).
            items = [f"{k}={_format_value(vv)}" for k, vv in v.items()
                     if vv is not None and k not in ("available",)]
            return " · ".join(items)
        if isinstance(v, list):
            return f"({len(v)} entries)"
        return str(v)
    def _sub(m):
        n = int(m.group(1))
        path = m.group(2)
        if not (1 <= n <= len(history)):
            return ""
        result = history[n - 1].result
        if path == "@count":
            for k in ("available_total", "ok_count", "used"):
                v = result.get(k)
                if v is not None:
                    return str(v)
            # Fallback su prima list (entries/results/items o altro).
            for k in ("entries", "results", "items"):
                v = result.get(k)
                if isinstance(v, list):
                    return str(len(v))
            lst = _find_list_of_dicts(result)
            return str(len(lst)) if lst else "0"
        v = _resolve_dotted(result, path)
        return _format_value(v)
    return _STEPREF_RE.sub(_sub, template)


# ── Main execution ──────────────────────────────────────────────────────

def execute_framework(framework: dict, *,
                       invoke_executor: Callable[[str, dict], dict],
                       llm_call_fast: Optional[Callable] = None,
                       query_context: str = "",
                       vaglio_judge: Optional[Callable] = None,
                       max_steps: int = 12,
                       remediate_args_cb: Optional[Callable] = None,
                       intent_hash: str = "",
                       runtime_ctx: Optional[dict] = None,
                       ) -> PraxisRun:
    """Esegue il framework deterministicamente.

    Args:
      framework: {steps, fillers, final_message}.
      invoke_executor: callable (tool_name, args) -> result dict.
      llm_call_fast: LLM fast tier per fillers (system, user, max_tokens, think).
      query_context: query utente originale (passato ai fillers).
      vaglio_judge: optional gate post-step (per ora opt-in).
      max_steps: cap step (default 12, allineato a §4.4).

    Returns: PraxisRun.
    """
    from praxis import compute_framework_hash
    run = PraxisRun()
    run.framework_hash = compute_framework_hash(framework)
    t_start = time.time()
    steps_spec = framework.get("steps") or []
    fillers = framework.get("fillers") or {}

    for i, step in enumerate(steps_spec):
        if i >= max_steps:
            run.aborted_reason = f"cap_steps {max_steps}"
            break
        tool = step.get("tool")
        raw_args = step.get("args") or {}
        if not tool:
            run.aborted_reason = f"step_{i+1}_no_tool"
            break

        # Branching dichiarativo (G2): skip step se condizione falsa.
        # Pattern: step.if_prev_entries_nonempty=true → skip se previous step
        # ritorna entries vuoto. Anti-pattern mail spam (filter 0 → move fail).
        if not _step_condition_passes(step, run.steps):
            if verbose:
                pass  # silent skip
            continue

        # Terminator
        if tool == "final_answer":
            run.final_text = _render_final_message(
                framework.get("final_message") or "", run.steps)
            run.final_kind = "answer"
            break

        # Resolve from_step + ${stepN.field} + fillers + ${RUNTIME:*} (in ordine)
        args1 = _resolve_from_step(raw_args, run.steps)
        args2 = {k: _resolve_stepref(v, run.steps) for k, v in args1.items()}
        args3 = _resolve_fillers(args2, fillers,
                                   llm_call_fast=llm_call_fast,
                                   query_context=query_context,
                                   intent_hash=intent_hash)
        args3 = _resolve_runtime_placeholders(args3, runtime_ctx or {})

        t0 = time.time()
        try:
            result = invoke_executor(tool, args3)
        except Exception as ex:
            log.warning("praxis_executor: %s raised %r", tool, ex)
            result = {"ok": False, "error": str(ex),
                       "error_class": "exception"}
        # Recovery deterministico ADR 0161 ext (G2): tenta 1 retry con args
        # remediate via args_extractor.regex_extract se rilevato pattern di
        # arg mancante. Convergenza universale §7.3: copre error_class
        # invalid_args, error_code ERR_ARG_*, e pattern testuali (executor
        # diversi usano convenzioni diverse).
        _r = result or {}
        _err_txt = str(_r.get("error") or "").lower()
        _needs_recovery = (
            not _r.get("ok")
            and remediate_args_cb is not None
            and (_r.get("error_class") == "invalid_args"
                 or _r.get("error_code") in (
                     "ERR_ARG_MISSING", "ERR_ARG_INVALID")
                 or "missing required arg" in _err_txt
                 or "argomento obbligatorio" in _err_txt
                 or "argomento non valido" in _err_txt)
        )
        if _needs_recovery:
            try:
                fixed_args = remediate_args_cb(
                    tool=tool, args=args3, result=result,
                    query_context=query_context,
                )
                if fixed_args and fixed_args != args3:
                    log.info("praxis_executor: retry %s with remediated args",
                              tool)
                    args3 = fixed_args
                    result = invoke_executor(tool, args3)
            except Exception as ex:
                log.warning("praxis_executor: remediate_args_cb raised %r",
                             ex)
        latency = int((time.time() - t0) * 1000)
        ok = bool((result or {}).get("ok"))
        run.steps.append(StepRun(
            step_idx=i + 1, tool=tool, args=args3,
            result=result or {}, ok=ok, latency_ms=latency,
        ))
        if ok:
            run.ok_count += 1

        # Vaglio gate (opt-in)
        if vaglio_judge is not None and not _vaglio_passes(
                vaglio_judge, tool, args3, result):
            run.aborted_reason = f"step_{i+1}_vaglio_block"
            run.final_kind = "blocked"
            break

        # Critical failure → stop
        if not ok and not _is_recoverable_error(result or {}):
            run.aborted_reason = f"step_{i+1}_failed"
            run.final_text = (result or {}).get("error") or "errore"
            run.final_kind = "error"
            break

    # Default final_text se non chiuso da final_answer
    if not run.final_text and not run.aborted_reason:
        run.final_text = _render_final_message(
            framework.get("final_message") or "", run.steps)

    run.elapsed_ms = int((time.time() - t_start) * 1000)
    return run


def _vaglio_passes(judge: Callable, tool: str, args: dict,
                    result: dict) -> bool:
    try:
        verdict = judge(tool=tool, args=args, result=result)
        if verdict is None:
            return True
        if isinstance(verdict, dict):
            return bool(verdict.get("ok", True))
        return True
    except Exception as ex:
        log.warning("praxis_executor: vaglio raised %r — fail-open", ex)
        return True


def _step_condition_passes(step: dict, history: list) -> bool:
    """Branching dichiarativo §7.9: ritorna False se step deve essere skipped.

    Pattern (G2 MVP):
      - step.if_prev_entries_nonempty: True → skip se prev step ha entries vuote.
      - step.if_prev_ok_count_gt: int → skip se prev.ok_count <= int.
      - step.if_prev_field_in: {"field":F, "values":[...]} → skip se prev[F] not in values.

    Senza condition keys → step esegue normalmente.
    """
    if not isinstance(step, dict) or not history:
        return True  # no history → primo step, esegue sempre
    prev = history[-1].result if isinstance(history[-1].result, dict) else {}
    if step.get("if_prev_entries_nonempty") is True:
        entries = prev.get("entries") or prev.get("results") or []
        if not isinstance(entries, list) or len(entries) == 0:
            return False
    cond = step.get("if_prev_ok_count_gt")
    if isinstance(cond, int):
        if (prev.get("ok_count") or 0) <= cond:
            return False
    cond = step.get("if_prev_field_in")
    if isinstance(cond, dict) and cond.get("field"):
        field = cond["field"]
        values = cond.get("values") or []
        if prev.get(field) not in values:
            return False
    return True


def _is_recoverable_error(result: dict) -> bool:
    ec = result.get("error_class") or ""
    return ec in ("invalid_args", "needs_inputs", "auth_required",
                   "binary_missing")


# ── High-level entry point per agent_runtime ────────────────────────────

def try_praxis_path(*, query: str, catalog: list,
                     invoke_executor_cb: Callable,
                     llm_call_wise: Optional[Callable] = None,
                     llm_call_fast: Optional[Callable] = None,
                     remediate_args_cb: Optional[Callable] = None,
                     lang: str = "it",
                     runtime_ctx: Optional[dict] = None,
                     verbose: bool = False) -> Optional[dict]:
    """Cascata Praxis: intent_extractor → cache hit O(1) → LLM 1-shot → execute.

    Args:
      query: query utente raw.
      catalog: list[Executor] visibili (composer).
      invoke_executor_cb: callable (tool_name, args) -> result dict.
      llm_call_wise: callable per L2 propose (wise tier, think=True).
      llm_call_fast: callable per intent_extractor + filler args (fast).
      lang: lingua prompt.
      verbose: stampe diagnostiche.

    Returns:
      None se cascata fallisce (no intent / no framework / parsing error).
      dict {steps, final_text, final_kind, framework, framework_hash,
            intent_sig, match_source} pronto per il caller.
    """
    if not query or not query.strip():
        return None
    try:
        from praxis import (get_store, extract_keywords,
                              compute_framework_hash)
        from praxis_propose import propose_framework
        from intent_extractor import extract_intent
    except Exception as ex:
        log.warning("praxis.try_praxis_path: import failed: %r", ex)
        return None

    # 1. Intent extraction
    if llm_call_fast is None:
        log.debug("praxis.try_praxis_path: no llm_call_fast → skip")
        return None
    intent = None
    try:
        intent = extract_intent(query, llm_call_fast)
    except Exception as ex:
        log.warning("praxis.try_praxis_path: extract_intent failed: %r", ex)
    if not intent or not intent.get("verb") or not intent.get("object"):
        if verbose:
            print(f"[praxis] no intent extracted ({intent!r})")
        return None
    verb = intent["verb"]
    obj = intent["object"]
    keywords = extract_keywords(query)

    # 2. Cache O(1) lookup
    store = get_store()
    excluded = store.excluded_framework_hashes(verb, obj, keywords)
    framework = None
    match_source = None
    intent_sig = None

    hit = store.try_match(verb, obj, keywords,
                            exclude_framework_hashes=excluded,
                            query=query)
    if hit:
        framework = hit["framework"]
        match_source = f"cache_{hit['match_kind']}"
        intent_sig = hit.get("skill_id", "")
        if verbose:
            print(f"[praxis] CACHE HIT skill={hit['skill_id']!r} "
                   f"kind={hit['match_kind']} stats={hit.get('stats')}")
    else:
        # 3. LLM 1-shot propose
        if llm_call_wise is None:
            if verbose:
                print("[praxis] cache miss + no llm_call_wise → fallback")
            return None
        # Pool filter universale (ADR 0136): esclude provider-suffixed tools
        # se query NO marker, ed esclude canonical se marker presente con
        # provider equivalente. Deterministico §7.9, riusa la stessa logica
        # di tool_grammar usata dal PLANNER legacy.
        try:
            from tool_grammar import filter_pool_for_grammar
            filtered_catalog, _excluded = filter_pool_for_grammar(
                catalog, user_query=query, proximity_markers=()
            )
        except Exception:
            filtered_catalog = catalog
        available = [e.name for e in filtered_catalog
                      if getattr(e, "name", None)]
        if "final_answer" not in available:
            available.append("final_answer")
        try:
            # Grammar GBNF MANDATORY (CRITICO #1, 25/5/2026): vincola output
            # LLM a JSON valido. Default attivo dal 26/5. Fallback a parsing
            # tollerante se provider non supporta grammar (raro).
            framework = propose_framework(
                query=query,
                intent={"verb": verb, "object": obj, "keywords": keywords},
                available_tools=available,
                excluded_frameworks=excluded,
                llm_call=llm_call_wise,
                lang=lang,
                use_grammar=True,
            )
        except Exception as ex:
            log.warning("praxis.try_praxis_path: propose failed: %r", ex)
            return None
        if not framework:
            if verbose:
                print("[praxis] propose returned None (parse failed)")
            return None
        match_source = "propose_llm"
        if verbose:
            print(f"[praxis] PROPOSE → {len(framework.get('steps') or [])} steps")

    # 4. Execute framework deterministicamente
    from praxis import compute_intent_sig
    _, _ihash = compute_intent_sig(verb, obj, keywords)
    run = execute_framework(
        framework,
        invoke_executor=invoke_executor_cb,
        llm_call_fast=llm_call_fast,
        query_context=query,
        remediate_args_cb=remediate_args_cb,
        intent_hash=_ihash,
        runtime_ctx=runtime_ctx,
    )

    if verbose:
        print(f"[praxis] exec done: {len(run.steps)} steps, ok_count={run.ok_count}, "
               f"elapsed={run.elapsed_ms}ms, aborted={run.aborted_reason!r}")

    # PRONOIA intervention (ADR 0161 ext, tetrade): se exec produce kind=error
    # E env attivo → ri-prova con prompt recovery contestuale.
    import os as _os
    _pronoia_active = (_os.environ.get("METNOS_PRONOIA", "1") == "1")
    if (run.final_kind == "error" and _pronoia_active):
        try:
            import pronoia
            if verbose:
                print(f"[praxis] kind=error → invoking Pronoia")
            recovery_res = pronoia.intervene(
                query=query,
                intent={"verb": verb, "object": obj, "keywords": keywords},
                catalog=catalog,
                praxis_run=run,
                invoke_executor_cb=invoke_executor_cb,
                llm_call_wise=llm_call_wise,
                llm_call_fast=llm_call_fast,
                remediate_args_cb=remediate_args_cb,
                lang=lang,
                verbose=verbose,
            )
            if recovery_res and recovery_res.get("final_kind") == "answer":
                if verbose:
                    print(f"[pronoia] recovered with {recovery_res['match_source']}")
                # Merge steps: original failed + pronoia recovery
                merged_steps = list(run.steps) + list(recovery_res["steps"])
                return {
                    "steps": merged_steps,
                    "final_text": recovery_res["final_text"],
                    "final_kind": "answer",
                    "framework": recovery_res["framework"],
                    "framework_hash": recovery_res["framework_hash"],
                    "intent_sig": intent_sig,
                    "verb": verb,
                    "object": obj,
                    "keywords": keywords,
                    "match_source": "pronoia",
                    "elapsed_ms": run.elapsed_ms + recovery_res["elapsed_ms"],
                    "aborted_reason": "",
                }
            elif verbose:
                print(f"[pronoia] non riuscito a recuperare")
        except Exception as ex:
            log.warning("pronoia intervention failed: %r", ex)

    return {
        "steps": run.steps,
        "final_text": run.final_text,
        "final_kind": run.final_kind,
        "framework": framework,
        "framework_hash": run.framework_hash,
        "intent_sig": intent_sig,
        "verb": verb,
        "object": obj,
        "keywords": keywords,
        "match_source": match_source,
        "elapsed_ms": run.elapsed_ms,
        "aborted_reason": run.aborted_reason,
    }
