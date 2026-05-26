"""pronoia_classify_fail.py — classify user ✗ in {format, args, pipeline}.

Estensione Pronoia (ADR 0161 ext, 26/5/2026). Granularita' feedback ✗:
invece di penalizzare in blocco la skill (executor + args + template),
LLM judge identifica la causa root del fail, dispatch differenziato:
  format_fail   → template needs_review, skill resta active
  args_fail     → filler_cache stale, skill resta active
  pipeline_fail → fail_count++ skill, maybe anti_skill (status quo)

Latency ~2-3s Gemma 26B middle (think=False). Chiamato SOLO su click ✗
utente esplicito (evento raro, fase di apprendimento). Disable a sistema
stabile con METNOS_PRONOIA_CLASSIFY_FAIL=0.

Determinismo §7.9: LLM judge, dispatch deterministic.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional, Callable

log = logging.getLogger(__name__)

from praxis_constants import (
    PRONOIA_DEFAULT_CLASS as DEFAULT_CLASS,
    PRONOIA_CLASSIFY_ENABLED,
)

VALID_CLASSES = ("format_fail", "args_fail", "pipeline_fail")


def is_enabled() -> bool:
    return PRONOIA_CLASSIFY_ENABLED


def _build_prompt(query: str, framework: dict, results: list[dict],
                   final_message: str) -> tuple[str, str]:
    """Costruisce prompt MATCH/NO_MATCH strutturale per classifier."""
    steps_summary = []
    for i, (step, res) in enumerate(zip(
            framework.get("steps", []), results), start=1):
        tool = step.get("tool", "?")
        args = step.get("args", {})
        ok = res.get("ok", False)
        result_keys = list(res.keys())[:5]
        steps_summary.append(
            f"step{i}: {tool} args={json.dumps(args, ensure_ascii=False)[:200]}\n"
            f"  ok={ok} result_keys={result_keys}"
        )
    steps_txt = "\n".join(steps_summary) or "(no steps)"
    template = framework.get("final_message", "")

    system = """Sei un classificatore di errori per agente AI.
L'utente ha cliccato ✗ su una pipeline appena eseguita.
Classifichi la causa root del fail in UNA fra 3 classi:

format_fail
  MATCH:
    - tutti gli step ok=true
    - executor e args sono coerenti con la query
    - SOLO il messaggio finale e' incompleto/mancante/non leggibile
    - placeholder ${stepN.X} non risolti, numero mancante, template hardcoded
  NO_MATCH:
    - executor sbagliato, args sbagliati, pipeline incompleta

args_fail
  MATCH:
    - executor corretto (verbo+oggetto coerenti con query)
    - args sbagliati o mancanti (path inesistente, valore vuoto, filler non risolto)
    - result.ok=false con errore di parametro o lista vuota inattesa
  NO_MATCH:
    - tutti gli args coerenti, executor sbagliato, formattazione finale errata

pipeline_fail
  MATCH:
    - pipeline strutturalmente sbagliata (executor errato, step inutili o mancanti)
    - executor giusto ma sequenza step errata per l'intent
    - tutto il flusso e' da rifare con framework diverso
  NO_MATCH:
    - solo template finale o singoli args sbagliati

Rispondi SOLO con UNA delle 3 stringhe: format_fail, args_fail, pipeline_fail.
Aggiungi UNA frase di motivazione dopo la classe, separata da '|'.
Esempio output: "format_fail|template senza placeholder, numero mancante"."""

    user = (
        f"QUERY UTENTE: {query}\n\n"
        f"PIPELINE ESEGUITA:\n{steps_txt}\n\n"
        f"TEMPLATE FINAL_MESSAGE: {template!r}\n"
        f"OUTPUT FINALE MOSTRATO ALL'UTENTE: {final_message!r}\n\n"
        f"Classifica il fail in UNA fra format_fail | args_fail | pipeline_fail."
    )
    return system, user


def _parse_response(raw: str) -> tuple[str, str]:
    """Estrae (class, reason) dall'output LLM. Fallback DEFAULT_CLASS se ambiguo."""
    if not raw:
        return DEFAULT_CLASS, "empty_llm_response"
    text = raw.strip().lower()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = text.strip()
    # Match canonical: cerca la prima classe valida nella prima riga.
    first_line = text.split("\n", 1)[0]
    cls = DEFAULT_CLASS
    for c in VALID_CLASSES:
        if c in first_line:
            cls = c
            break
    # Reason: tutto dopo '|' o seconda riga.
    reason = ""
    if "|" in first_line:
        reason = first_line.split("|", 1)[1].strip()
    elif "\n" in text:
        reason = text.split("\n", 1)[1].strip()[:120]
    return cls, reason or "no_reason"


def classify_fail(query: str,
                   framework: dict,
                   results: list[dict],
                   final_message: str,
                   llm_call: Optional[Callable] = None
                   ) -> dict:
    """Classifica una pipeline failed in format/args/pipeline.

    Returns:
      {"class": str, "reason": str, "elapsed_ms": int}.
      Class in VALID_CLASSES. Se disabilitato o LLM mancante,
      ritorna DEFAULT_CLASS con reason="disabled" o "no_llm".
    """
    if not is_enabled():
        return {"class": DEFAULT_CLASS, "reason": "disabled",
                "elapsed_ms": 0}
    if llm_call is None:
        return {"class": DEFAULT_CLASS, "reason": "no_llm",
                "elapsed_ms": 0}
    import time
    t0 = time.monotonic()
    system, user = _build_prompt(query, framework, results, final_message)
    try:
        raw = llm_call(system, user, max_tokens=80, think=False)
    except Exception as ex:
        log.warning("pronoia_classify_fail: llm call failed: %r", ex)
        return {"class": DEFAULT_CLASS, "reason": f"llm_error:{ex!r}",
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    cls, reason = _parse_response(raw or "")
    return {"class": cls, "reason": reason,
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}
