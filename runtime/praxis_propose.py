"""Praxis L_propose — Mētis (Μῆτις), il consigliere strategico astuto.

    «E' la suggeritrice silenziosa che agisce dall'interno della mente
     — come ha fatto dentro Zeus — per risolvere problemi complessi.»

Nella triade Praxis Engine (ADR 0161):
  Mētis (questo modulo) propone il piano in 1 call wise.
  Noûs (praxis_executor)  esegue deterministico, senza esitazione.
  Praxis (praxis.py)      ricorda cio' che ha funzionato.

Nel mito Zeus ingoia Mētis per averla dentro come consigliera silenziosa.
Qui Mētis vive dentro Praxis: parla solo quando il catalog non sa, tace
quando l'esperienza consolidata basta. Ogni feedback ✓ la silenzia un po'
di piu', fino al limite asintotico dove Metnos non chiede piu' consigli.

La sua specialita': l'espediente tecnico, il piano astuto, la strategia
risolutiva. Non l'esecuzione (= Noûs), non la memoria (= Praxis). Solo
il SAGGIO CONSIGLIO, dato una volta, in forma compatta (JSON framework).

Praxis L_propose — LLM-1-shot framework proposer.

Date query+intent (verb, object, keywords), il LLM (Gemma 4 26B wise tier,
think=True, budget 2048) propone in UNA sola call l'intero framework
multi-step in JSON strutturato:

  {
    "steps": [
      {"tool": "<executor_name>", "args": {<args_o_FILLER_placeholder>}},
      ...
    ],
    "fillers": {
      "<filler_name>": {"prompt": "<question_1_line>",
                          "default": "<fallback>",
                          "tier": "fast"}
    },
    "final_message": "<template_con_${stepN.field}>"
  }

Vantaggi vs PLANNER step-by-step (legacy):
- 1 chiamata LLM (~15-20s wise) vs 5-7 chiamate (~80s totali).
- Coerenza globale: il LLM vede tutto il piano, non perde la rotta mid-stream.
- Output strutturato vincolato da grammar GBNF → no parser fragile.
- Anti-fixation: excluded_frameworks impedisce di riproporre path falliti.

Pipeline:
  query → praxis_propose.propose_framework(...) → framework JSON
       → executor_engine esegue deterministico, filler runtime se serve

Filosofia §7.9: LLM solo dove deterministico non basta. Qui il LLM serve
perche' la "discovery" del piano da una query NL e' linguistica/creativa,
non una scelta deterministica.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

try:
    import prompt_loader
    from config import DEFAULT_LANG
except Exception:
    from runtime import prompt_loader  # pragma: no cover
    from runtime.config import DEFAULT_LANG

log = logging.getLogger(__name__)


# GBNF grammar per output JSON strutturato (vincola LLM a forma valida).
GRAMMAR_FRAMEWORK = r"""
root ::= "{" ws "\"steps\":" ws stepList ("," ws "\"fillers\":" ws fillerObj)?
          ("," ws "\"final_message\":" ws string)? ws "}"
stepList ::= "[" ws step ("," ws step)* ws "]"
step ::= "{" ws "\"tool\":" ws string "," ws "\"args\":" ws args ws "}"
args ::= "{" ws "}" | "{" ws kv ("," ws kv)* ws "}"
kv ::= string ":" ws value
value ::= string | number | "true" | "false" | "null" | args | arrayVal
arrayVal ::= "[" ws "]" | "[" ws value ("," ws value)* ws "]"
fillerObj ::= "{" ws "}" | "{" ws fk ("," ws fk)* ws "}"
fk ::= string ":" ws fillerVal
fillerVal ::= "{" ws "\"prompt\":" ws string
              ("," ws "\"default\":" ws string)?
              ("," ws "\"tier\":" ws string)? ws "}"
string ::= "\"" charSeq "\""
charSeq ::= ([^"\\] | "\\" anyEscape)*
anyEscape ::= ["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number ::= "-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)? ([eE] [+-]? [0-9]+)?
ws ::= [ \t\n\r]*
"""


def propose_framework(*, query: str, intent: dict,
                       available_tools: list[str],
                       excluded_frameworks: Optional[set[str]] = None,
                       llm_call,
                       lang: Optional[str] = None,
                       use_grammar: bool = True,
                       recovery_context: Optional[dict] = None,
                       ) -> Optional[dict]:
    """LLM 1-shot framework generation.

    Args:
      query: richiesta utente raw.
      intent: dict {verb, object, keywords?}.
      available_tools: lista nomi executor visibili.
      excluded_frameworks: set di framework_hash da NON riproporre
        (anti_skills o ↻ repeat in-memory).
      llm_call: callable (system, user, max_tokens=, think=, grammar=?) -> str.
      lang: lingua prompt (default config.DEFAULT_LANG).
      use_grammar: vincola output con GBNF se LLM provider lo supporta.

    Returns:
      framework dict o None se parsing/LLM fallisce.
    """
    if not query or not query.strip():
        return None
    if not intent or not intent.get("verb"):
        return None

    lang = lang or DEFAULT_LANG
    verb = (intent.get("verb") or "").strip()
    obj = (intent.get("object") or "").strip()
    keywords = intent.get("keywords") or []
    excl = excluded_frameworks or set()

    tools_inline = ", ".join(sorted(set(available_tools))[:80])
    excluded_note = ("[nessuno]" if not excl else
                      f"NON riproporre le pipeline con shape-hash: "
                      f"{sorted(excl)[:5]}")

    # Pronoia recovery prompt se recovery_context fornito.
    prompt_role = "pronoia_recovery" if recovery_context else "praxis_propose"
    prompt = prompt_loader.get(
        prompt_role,
        lang,
        verb=verb,
        obj=obj,
        keywords=", ".join(keywords) if keywords else "[nessuna]",
        tools=tools_inline,
        excluded_note=excluded_note,
        recovery_context=recovery_context or {},
    )
    try:
        # Grammar+thinking osservato collidere su max_tokens (ADR 0133 nota).
        # Con grammar attivo → think=False per safety.
        kwargs: dict = {"max_tokens": 2048}
        if use_grammar:
            kwargs["grammar"] = GRAMMAR_FRAMEWORK
            kwargs["think"] = False
        else:
            kwargs["think"] = True
        raw = llm_call(prompt, query, **kwargs)
    except TypeError:
        # llm_call non supporta grammar/think kwargs
        try:
            raw = llm_call(prompt, query, max_tokens=2048)
        except Exception as ex:
            log.warning("praxis_propose: llm_call (fallback) failed: %r", ex)
            return None
    except Exception as ex:
        log.warning("praxis_propose: llm_call failed: %r", ex)
        return None

    return _parse_framework_json(raw)


def _parse_framework_json(raw: str) -> Optional[dict]:
    """Estrae JSON strutturato dall'output LLM, tollera <think> e code fence."""
    if not raw:
        return None
    text = raw.strip()
    # Strip thinking tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|.*?\|>", "", text)
    # Strip code fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        # Recovery: primo `{` ... ultimo `}`
        i = text.find("{")
        j = text.rfind("}")
        if i < 0 or j <= i:
            return None
        try:
            data = json.loads(text[i:j + 1])
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    # Sanitize step shape
    out_steps = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        tool = s.get("tool")
        args = s.get("args")
        if not tool or not isinstance(tool, str):
            continue
        if args is None:
            args = {}
        if not isinstance(args, dict):
            continue
        out_steps.append({"tool": tool, "args": args})
    if not out_steps:
        return None
    return {
        "steps": out_steps,
        "fillers": data.get("fillers") if isinstance(data.get("fillers"),
                                                       dict) else {},
        "final_message": data.get("final_message", ""),
    }


# ── Filler resolution (LLM fast tier o default deterministico) ──────────

def resolve_filler(filler_name: str, filler_spec: dict, *,
                    llm_call=None, query_context: str = "",
                    intent_hash: str = "") -> str:
    """Risolve un filler: cache hit O(1) | prompt mini al LLM fast tier | default.

    CRITICO #2 (25/5/2026): cache `(intent_hash, filler_name) → value` lookup
    in praxis.sqlite. 1ª chiamata = LLM (1-3s), successive = sub-ms.
    """
    if not isinstance(filler_spec, dict):
        return ""
    default = str(filler_spec.get("default") or "")
    prompt = str(filler_spec.get("prompt") or "")

    # Cache lookup (sub-ms)
    if intent_hash and filler_name:
        try:
            from praxis import get_store
            cached = get_store().filler_cache_get(intent_hash, filler_name)
            if cached:
                return cached
        except Exception as ex:
            log.warning("filler_cache_get failed: %r", ex)

    if not prompt or llm_call is None:
        return default
    try:
        sys_msg = (
            "Sei un assistente Metnos. Rispondi con UNA SOLA parola/valore "
            "richiesto, senza spiegazioni, senza punteggiatura, senza "
            "virgolette. Nessun think."
        )
        user_msg = f"Contesto: {query_context}\n\n{prompt}"
        raw = llm_call(sys_msg, user_msg, max_tokens=24, think=False)
        val = (raw or "").strip().strip('"').strip("'").split("\n")[0].strip()
        val = val or default
        # Cache put per future hit
        if intent_hash and filler_name and val:
            try:
                from praxis import get_store
                get_store().filler_cache_put(intent_hash, filler_name, val)
            except Exception as ex:
                log.warning("filler_cache_put failed: %r", ex)
        return val
    except Exception as ex:
        log.warning("praxis_propose.resolve_filler %s: LLM failed (%r), "
                     "using default", filler_name, ex)
        return default
