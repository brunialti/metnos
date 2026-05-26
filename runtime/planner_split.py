# ╔════════════════════════════════════════════════════════════════════╗
# ║ REMOVED-PRAXIS-FINAL (25/5/2026 sera tardissima)                    ║
# ║ Sostituito da pentade Mētis+Noûs+Praxis+Pronoia+Aporia (ADR 0161).   ║
# ║ Bench Gemma 26B locale 30/35 (85%) > Opus 29/35 (82%).              ║
# ║ NESSUN codepath attivo lo importa. File orphan, removal fisica      ║
# ║ pianificata post 24h monitor live use.                              ║
# ╚════════════════════════════════════════════════════════════════════╝
"""planner_split.py — split opt-in del PLANNER call in 2 call sequenziali
(#H0c, 19/5/2026 v3, post-bench #H0a).

Razionale (bench #H0a, 19/5/2026 sera/v2):
  Anche dopo slim (b)+(c) la singola call `provider.chat_with_tools(...)`
  costa median ~15.7s end-to-end (Gemma 4 26B think=True budget=512) per
  step PLANNER, con 10-15k tok input. Lo split sposta la decisione tool
  in due fasi:
    1) SELECTOR — system minimo (~300 tok) + tools_for_step rendered
       SOLO come name+desc-1-frase (~700 tok totali). Grammar GBNF enum
       di nomi → output `{"name":"X","arguments":{}}`. think=False.
       Latenza attesa ~0.8-2s.
    2) ARGS FILLER — system minimo (~300 tok) + schema args del SOLO
       tool scelto (~500 tok). Grammar GBNF tool-specifica (via
       `tool_grammar.generate_tool_grammar([chosen_tool])`). think=False.
       Latenza attesa ~1-2s.

  Speedup atteso end-to-end: 15.7s → ~2-4s = 4-8× ulteriore vs SLIM+SMART.

Interaction grammar GBNF (ADR 0133 ext):
  - Selector grammar minimal: discriminated union dei nomi del pool +
    synthetic `final_answer` (step≥2) e `request_disambiguation_from_user`
    (step==1) come per `generate_tool_grammar`.
    Output forzato: `{"name":"<tool>","arguments":{}}`.
  - Args grammar: full schema-driven via `generate_tool_grammar([chosen])`.
    Vincola args ai required + types del tool scelto.

Failure modes (fallback al monolitico, no double-fail):
  - selector non emette tool_call valido (grammar match fail, raro) →
    SplitFailure("selector_no_tool")
  - chosen name non e' nel pool (impossibile con grammar, paranoid check) →
    SplitFailure("selector_off_pool")
  - args filler non emette tool_call valido → SplitFailure("args_no_tool")
  - validate_tool_call(args) sul tool_call ricostruito fallisce →
    SplitFailure("args_validation_failed")

Output shape: `ToolUseResult` (drop-in con monolitico):
  - tool_calls: [ToolCall(name=chosen, arguments=filled_args, ...)]
  - in_tokens/out_tokens/latency_ms: SOMMA delle 2 call

Opt-in: env `METNOS_PLANNER_SPLIT=1`. Default OFF. Smoke gate prima di
default-on (#H0c).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

log = logging.getLogger(__name__)
from dataclasses import dataclass, field
from typing import Optional, Any, Sequence


# Re-export per i caller (agent_runtime), evita import cross-module.
from llm_provider import ToolCall, ToolUseResult, ProviderError
from tool_grammar import (
    generate_tool_grammar,
    validate_tool_call,
    FINAL_ANSWER_TOOL_NAME,
    DISAMBIG_TOOL_NAME,
    _emit_primitives,
    _sanitize_rule_name,
    _extract_name,
)


# --- Public API ---------------------------------------------------------

@dataclass
class SplitTelemetry:
    """Breakdown latenza/tok per debugging + telemetria (#H0c)."""
    selector_latency_ms: int = 0
    args_latency_ms: int = 0
    selector_in_tok: int = 0
    selector_out_tok: int = 0
    args_in_tok: int = 0
    args_out_tok: int = 0
    chosen_tool: str = ""
    failure_reason: str = ""


class SplitFailure(Exception):
    """Sollevata quando lo split non puo' procedere. Il caller cattura e
    fa fallback al `chat_with_tools` monolitico."""
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def is_split_enabled() -> bool:
    """Opt-in via env. Default OFF: smoke gate prima di default-on (#H0c)."""
    return os.environ.get("METNOS_PLANNER_SPLIT", "0") == "1"


def should_split_proactive(
    lang: str,
    verb_hint: Optional[str] = None,
    intent_confidence: Optional[float] = None,
    top_rank_distance: Optional[float] = None,
) -> bool:
    """Gate proattivo (#H0e wire-in 19/5/2026): decide se invocare lo split
    basato sulla calibration per-lingua + segnali da intent_extractor +
    prefilter. Caller-side opt-in: usalo in `agent_runtime` PRIMA di
    `chat_with_tools_split` per skippare split su query "facili" (1 tool
    ovviamente vincitore) o per verbi mutating (safety alta).

    Ritorna False (skip split, vai monolithic) se:
      - intent_confidence < threshold per il verb (o threshold_default)
      - top_rank_distance < rank_distance_min (prefilter incerto)
    Ritorna True altrimenti, o se i segnali sono None (best-effort).

    §7.9 deterministico: lookup table + comparazione numerica. Nessun LLM.
    """
    if not is_split_enabled():
        return False
    try:
        from calibration_check import ensure_calibration  # type: ignore
        cal = ensure_calibration(lang)
    except Exception as ex:
        log.debug("calibration_check unavailable, fallback split=True: %s", ex)
        return True
    if intent_confidence is not None:
        thr = cal.get("threshold_by_verb", {}).get(
            verb_hint or "", cal.get("threshold_default", 0.80))
        if intent_confidence < thr:
            return False
    if top_rank_distance is not None:
        rd_min = cal.get("rank_distance_min", 0.15)
        if top_rank_distance < rd_min:
            return False
    return True


def is_provider_supported(provider) -> bool:
    """Solo llamacpp supporta grammar GBNF — il provider attuale dipende
    da `cache_prompt + grammar` paylod. Anthropic/OpenAI (frontier) NON
    sono supportati: lo split non ha valore su provider remoti dove la
    latenza e' dominata da network round-trip."""
    return getattr(provider, "name", "") == "llamacpp"


# --- Selector grammar (minimal: emette solo `{"name":"X","arguments":{}}`)

def build_selector_grammar(tools: Sequence[Any], *,
                            allow_final_answer: bool = False,
                            allow_disambiguation: bool = False) -> str:
    """Costruisce GBNF che vincola l'output a
    `{"name":"<tool>","arguments":{}}` con `<tool>` enumerato dal pool.

    A differenza di `tool_grammar.generate_tool_grammar`, NON emette
    `args_<tool>` rules: la fase ARGS filler ricostruisce gli args con
    grammar dedicata. Risparmio tokens decisore + latenza.

    Args:
      tools: pool del catalogo. Ogni tool deve avere `function.name`.
      allow_final_answer: aggiunge synthetic `final_answer` (step>=2).
      allow_disambiguation: aggiunge synthetic `request_disambiguation_from_user`
        (step==1).

    Ritorna stringa GBNF o stringa vuota se il pool e' vuoto (caller
    deve fallback al monolitico).
    """
    names: list[str] = []
    for t in tools:
        n = _extract_name(t)
        if n and n not in names:
            names.append(n)
    if not names:
        return ""

    if allow_final_answer and FINAL_ANSWER_TOOL_NAME not in names:
        names.append(FINAL_ANSWER_TOOL_NAME)
    if allow_disambiguation and DISAMBIG_TOOL_NAME not in names:
        names.append(DISAMBIG_TOOL_NAME)

    # Primitives strettamente necessarie: ws, sep, colon.
    used = {"ws", "sep", "colon"}
    prims = _emit_primitives(used)

    # name ::= "\"tool1\"" | "\"tool2\"" | ...
    name_alts = " | ".join(f'"\\"{n}\\""' for n in names)
    rules = list(prims) + [
        "",
        # root vincola structure JSON + arguments forzato a `{}` literal.
        'root ::= "{" ws "\\"name\\"" colon name sep "\\"arguments\\"" colon "{}" ws "}"',
        f"name ::= {name_alts}",
    ]
    return "\n".join(rules)


# --- Selector / args prompts (minimal, IT) ------------------------------

def _build_selector_prompt(query: str, tools: Sequence[Any], *,
                            allow_final_answer: bool,
                            allow_disambiguation: bool) -> str:
    """Prompt minimo per il SELECTOR. Solo lista name + desc-1-frase, no
    schema args. Versione IT (PLANNER lingua locale §3 prompt-as-data).

    PIPELINE AWARENESS (fix 19/5/2026 v4 post-bench #H0c.2): se la
    conversation include observations da step precedenti, il SELECTOR
    DEVE scegliere il tool successivo della pipeline, non ripetere il
    precedente. Senza questa regola, su query mutating multi-step
    (es. "sposta i file .pdf vecchi") il LLM ripeteva find_files allo
    step 2 invece di passare a move_files (regression accuracy -6pp).
    """
    lines = [
        "Scegli UN tool dalla lista per la query utente.",
        "Output: SOLO il JSON `{\"name\":\"<tool>\",\"arguments\":{}}`.",
        "NON aggiungere prosa, NON ragionare ad alta voce.",
        "",
        "DEVI: se la conversation ha step precedenti (ruoli tool/assistant), "
        "scegli il tool che FA PROGREDIRE la pipeline verso il completamento "
        "della query (es. dopo find_files → move/delete/compress/send; dopo "
        "get_inputs → l'azione vera; dopo read_messages → describe/filter/move).",
        "NON DEVI: ripetere lo stesso tool dello step precedente quando l'osservazione "
        "e' gia' `ok:true`. Se l'observation contiene entries/results, il prossimo "
        "tool DEVE consumarli (filter/move/delete/describe/send), NON rifare la ricerca.",
        "DEVI: se nessuno step e' stato eseguito ancora, scegli il primo tool della "
        "pipeline (producer: read/find/list/get) oppure final_answer/disambig se appropriato.",
        "",
        "Tool disponibili (nome: descrizione breve):",
    ]
    for t in tools:
        fn = t.get("function", {}) if isinstance(t, dict) else {}
        name = fn.get("name", "?")
        desc = (fn.get("description") or "")
        # Tronca a 120 char + prima frase per coerenza con render selector.
        first_period = desc.find(". ")
        if 0 < first_period <= 200:
            desc = desc[:first_period + 1]
        if len(desc) > 200:
            desc = desc[:199].rstrip() + "…"
        lines.append(f"  - {name}: {desc}")
    if allow_final_answer:
        lines.append(f"  - {FINAL_ANSWER_TOOL_NAME}: chiude il turno "
                     "con un messaggio user-facing (step>=2).")
    if allow_disambiguation:
        lines.append(f"  - {DISAMBIG_TOOL_NAME}: chiede chiarimento all'utente "
                     "(step 1, ambiguita' irrisolta).")
    return "\n".join(lines)


def _build_args_prompt(query: str, chosen: dict) -> str:
    """Prompt minimo per ARGS FILLER. Solo schema args del singolo tool."""
    fn = chosen.get("function") or {}
    name = fn.get("name", "?")
    desc = fn.get("description", "")
    schema = fn.get("parameters") or {}
    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
    lines = [
        f"Riempi gli args per il tool `{name}` in base alla query utente.",
        "Output: SOLO JSON `{\"name\":\"" + name + "\",\"arguments\":{...}}`.",
        "NON aggiungere prosa, NON ragionare ad alta voce.",
        "",
        f"Tool: {name}",
        f"Descrizione: {desc}",
        f"Schema args:\n{schema_str}",
    ]
    return "\n".join(lines)


# --- Main entry point ---------------------------------------------------

def chat_with_tools_split(
    provider,
    system: str,            # planner_system (NON usato in split — sostituito da prompt minimi)
    user: str,              # user_query_for_run
    tools: Sequence[Any],   # tools_for_step (gia' slim via tool_schema_slim)
    history: Optional[list] = None,
    *,
    max_tokens: int = 4096,
    temperature: float = 0,
    allow_final_answer: bool = False,
    allow_disambiguation: bool = False,
    include_canonical_query: bool = False,  # ADR 0149 (no-op in v1, futuro)
    telemetry: Optional[SplitTelemetry] = None,
    verbose: bool = False,
    **_unused,  # think/reasoning_budget/grammar ignorati: split forza think=False
) -> ToolUseResult:
    """Drop-in del `provider.chat_with_tools` monolitico ma in 2 call:
    SELECTOR (grammar enum-of-names) + ARGS FILLER (grammar tool-specifica).

    Conserva il `system` originale come back-link di contesto: il selector
    riceve `system_selector` ricostruito, ma `system` viene preservato
    nella history->user roundtrip se ci sono observations precedenti.

    Solleva `SplitFailure` se non puo' completare lo split (caller fa
    fallback al monolitico).
    """
    if not is_provider_supported(provider):
        raise SplitFailure("provider_unsupported",
                           f"split richiede llamacpp, got {getattr(provider, 'name', '?')}")
    if not tools:
        raise SplitFailure("empty_pool", "tools=[] non separabile")

    tel = telemetry if telemetry is not None else SplitTelemetry()

    # --- STEP 1: SELECTOR -------------------------------------------------
    sel_grammar = build_selector_grammar(
        tools,
        allow_final_answer=allow_final_answer,
        allow_disambiguation=allow_disambiguation,
    )
    if not sel_grammar:
        raise SplitFailure("selector_grammar_empty",
                           "build_selector_grammar ha ritornato vuoto")
    sel_system = _build_selector_prompt(
        user, tools,
        allow_final_answer=allow_final_answer,
        allow_disambiguation=allow_disambiguation,
    )
    if verbose:
        print(f"[split.sel] grammar={len(sel_grammar)}ch system={len(sel_system)}ch "
              f"pool={len(tools)} tools allow_fa={allow_final_answer} "
              f"allow_dis={allow_disambiguation}")
    t0 = time.time()
    try:
        sel_res = provider.chat_with_tools(
            sel_system, user, [],     # tools=[] (grammar mode bypassa tools)
            history=history,
            max_tokens=256,           # selector: solo "{"name":"X","arguments":{}}" ~50 tok
            temperature=temperature,
            grammar=sel_grammar,
        )
    except ProviderError as e:
        raise SplitFailure("selector_provider_error", str(e)) from e
    sel_lat_ms = int((time.time() - t0) * 1000)
    tel.selector_latency_ms = sel_lat_ms
    tel.selector_in_tok = sel_res.in_tokens
    tel.selector_out_tok = sel_res.out_tokens

    if not sel_res.tool_calls:
        # Grammar mode dovrebbe SEMPRE produrre un tool_call valido; se no,
        # e' un bug del provider o grammar malformato.
        tel.failure_reason = "selector_no_tool"
        raise SplitFailure("selector_no_tool",
                           f"text={sel_res.text[:120]!r}")
    chosen_name = sel_res.tool_calls[0].name
    tel.chosen_tool = chosen_name

    # Synthetic tools: handle inline senza second call (args sono triviali
    # ma richiedono full context). Per final_answer/disambig facciamo
    # comunque il second call: il prompt + history determinano il message.
    chosen_tool: Optional[dict] = None
    is_synthetic = False
    if chosen_name == FINAL_ANSWER_TOOL_NAME and allow_final_answer:
        is_synthetic = True
        # Schema fittizio per il args filler.
        chosen_tool = {
            "type": "function",
            "function": {
                "name": FINAL_ANSWER_TOOL_NAME,
                "description": "Chiudi il turno con un messaggio user-facing.",
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            },
        }
    elif chosen_name == DISAMBIG_TOOL_NAME and allow_disambiguation:
        is_synthetic = True
        chosen_tool = {
            "type": "function",
            "function": {
                "name": DISAMBIG_TOOL_NAME,
                "description": "Chiedi disambiguazione all'utente.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["question", "options"],
                },
            },
        }
    else:
        for t in tools:
            if isinstance(t, dict) and (t.get("function") or {}).get("name") == chosen_name:
                chosen_tool = t
                break
    if chosen_tool is None:
        tel.failure_reason = "selector_off_pool"
        raise SplitFailure("selector_off_pool",
                           f"name={chosen_name!r} non e' nel pool")

    # --- STEP 2: ARGS FILLER ---------------------------------------------
    args_grammar: str
    if is_synthetic:
        # Per synthetics riusiamo `generate_tool_grammar` su [chosen_tool]
        # con il flag adatto: questo emette grammar per il singolo synthetic.
        # NB: `generate_tool_grammar([final_answer], allow_final_answer=True)`
        # genera SOLO pairFinalAnswer (single-tool case → singolo discriminated
        # alt = sintatticamente uguale a un pairTool fixed).
        args_grammar = generate_tool_grammar(
            [chosen_tool],
            allow_final_answer=(chosen_name == FINAL_ANSWER_TOOL_NAME),
            allow_disambiguation=(chosen_name == DISAMBIG_TOOL_NAME),
        )
    else:
        args_grammar = generate_tool_grammar(
            [chosen_tool],
            allow_final_answer=False,
            allow_disambiguation=False,
        )
    if not args_grammar:
        tel.failure_reason = "args_grammar_empty"
        raise SplitFailure("args_grammar_empty",
                           f"generate_tool_grammar([{chosen_name}]) vuoto")

    args_system = _build_args_prompt(user, chosen_tool)
    if verbose:
        print(f"[split.args] grammar={len(args_grammar)}ch system={len(args_system)}ch "
              f"chosen={chosen_name} synthetic={is_synthetic}")
    t1 = time.time()
    try:
        args_res = provider.chat_with_tools(
            args_system, user, [],
            history=history,
            max_tokens=max_tokens,    # full args possono essere lunghi
            temperature=temperature,
            grammar=args_grammar,
        )
    except ProviderError as e:
        tel.failure_reason = "args_provider_error"
        raise SplitFailure("args_provider_error", str(e)) from e
    args_lat_ms = int((time.time() - t1) * 1000)
    tel.args_latency_ms = args_lat_ms
    tel.args_in_tok = args_res.in_tokens
    tel.args_out_tok = args_res.out_tokens

    if not args_res.tool_calls:
        tel.failure_reason = "args_no_tool"
        raise SplitFailure("args_no_tool",
                           f"text={args_res.text[:120]!r}")
    args_call = args_res.tool_calls[0]
    if args_call.name != chosen_name:
        # Coerenza: la grammar dovrebbe forzare name==chosen, ma paranoid.
        tel.failure_reason = "args_name_mismatch"
        raise SplitFailure("args_name_mismatch",
                           f"selector={chosen_name!r} args={args_call.name!r}")

    # Validation top-level (Strategia 3 ADR 0133): required keys presenti.
    ok, err = validate_tool_call(
        {"name": args_call.name, "arguments": args_call.arguments},
        [chosen_tool],
        allow_final_answer=(chosen_name == FINAL_ANSWER_TOOL_NAME),
        allow_disambiguation=(chosen_name == DISAMBIG_TOOL_NAME),
    )
    if not ok:
        tel.failure_reason = "args_validation_failed"
        raise SplitFailure("args_validation_failed", err)

    # --- Output ToolUseResult drop-in -------------------------------------
    total_lat_ms = sel_lat_ms + args_lat_ms
    total_in_tok = sel_res.in_tokens + args_res.in_tokens
    total_out_tok = sel_res.out_tokens + args_res.out_tokens
    if verbose:
        print(f"[split.done] chosen={chosen_name} "
              f"sel={sel_lat_ms}ms args={args_lat_ms}ms total={total_lat_ms}ms "
              f"in={total_in_tok} out={total_out_tok}")
    return ToolUseResult(
        text="",
        tool_calls=[ToolCall(
            name=args_call.name,
            arguments=args_call.arguments,
            call_id=args_call.call_id or f"split_{int(time.time()*1000)}",
            canonical_query=args_call.canonical_query,
        )],
        in_tokens=total_in_tok,
        out_tokens=total_out_tok,
        model=getattr(provider, "model", ""),
        provider=getattr(provider, "name", "llamacpp"),
        latency_ms=total_lat_ms,
        thinking="",  # split mode forza think=False
    )


# --- CLI smoke (dev only) -----------------------------------------------

def _cli_smoke():
    """python3 runtime/planner_split.py — smoke test on llama-server :8080.

    Esegue 1 query × 4 tool ridotti, verifica che split produca ToolUseResult
    coerente. Non sostituisce il bench, solo conferma wire-in funzionante.
    """
    import sys
    from llm_provider import LlamaCppProvider
    print("[planner_split smoke] llama-server :8080")
    provider = LlamaCppProvider()
    tools = [
        {"type": "function", "function": {
            "name": "find_files",
            "description": "Trova file per pattern in directory.",
            "parameters": {"type": "object",
                           "properties": {"pattern": {"type": "string"},
                                          "root": {"type": "string"}},
                           "required": ["pattern"]},
        }},
        {"type": "function", "function": {
            "name": "find_dirs",
            "description": "Trova directory in path.",
            "parameters": {"type": "object",
                           "properties": {"root": {"type": "string"}},
                           "required": ["root"]},
        }},
        {"type": "function", "function": {
            "name": "get_now",
            "description": "Ritorna l'ora corrente.",
            "parameters": {"type": "object", "properties": {}},
        }},
    ]
    queries = [
        ("trova i file .py in /tmp", "find_files"),
        ("elenca directory in /opt", "find_dirs"),
        ("che ora è", "get_now"),
    ]
    for q, expected in queries:
        tel = SplitTelemetry()
        try:
            r = chat_with_tools_split(provider, "", q, tools, telemetry=tel, verbose=True)
            picked = r.tool_calls[0].name if r.tool_calls else "<no_tool>"
            ok = "✓" if picked == expected else "✗"
            print(f"  q={q!r} expected={expected} picked={picked} {ok} "
                  f"args={r.tool_calls[0].arguments if r.tool_calls else '-'}")
        except SplitFailure as e:
            print(f"  q={q!r} SPLIT FAIL: {e}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_smoke())
