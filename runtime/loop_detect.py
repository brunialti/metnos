"""loop_detect.py — Strategia E safety net (ADR 0133).

Rileva pattern di failure ripetuti del PLANNER: stesso `(tool, error_class)`
osservato in N step consecutivi indica che l'LLM e' bloccato in un loop e
non sta correggendo (consecutive_blocked + grammar + validator non bastano
nel caso residuo).

Determinismo §7.9. Niente LLM, niente IO. Funzione pura testabile.

Differenza vs `duplicate_call_blocked` (agent_runtime.py): quello blocca
chiamate con args ESATTAMENTE identici al precedente; loop_detect cattura
loop di fallimento dove gli args cambiano leggermente ad ogni step (es.
get_inputs con args diversi ma tutti senza `title` required → stesso
error_class `invalid_args`/`grammar_post_validate`).

Pattern d'uso (agent_runtime.py post-step):
    from loop_detect import is_repeated_failure
    if is_repeated_failure(log.steps + [step], threshold=2):
        log.final_kind = "loop_break"
        # final_message specifico
        return log
"""
from __future__ import annotations

from typing import Any, Sequence


def _step_signature(step: Any) -> tuple[str, str] | None:
    """Estrae `(tool_name, error_class)` da uno step, o None se step e' OK.

    Step puo' essere:
      - dataclass `Step` di agent_runtime (attr chosen_tool, result, error)
      - dict (test scenario)
    """
    if step is None:
        return None
    # Accesso uniforme attr o dict
    def _get(s, key):
        if isinstance(s, dict):
            return s.get(key)
        return getattr(s, key, None)
    tool = _get(step, "chosen_tool")
    if not tool:
        return None
    result = _get(step, "result")
    if not isinstance(result, dict):
        return None
    # Step OK = niente signature (loop e' di fallimenti).
    if result.get("ok"):
        return None
    # error_class esplicita ha priorita' (ADR 0101 per crawler, etc.).
    err_class = result.get("error_class")
    if not err_class:
        # Fallback: step.error campo (es. "grammar_post_validate",
        # "duplicate_call_blocked", "malformed_reference").
        err_class = _get(step, "error") or ""
        # Normalizza: prendi prima parola se string longa.
        if isinstance(err_class, str) and ":" in err_class:
            err_class = err_class.split(":", 1)[0].strip()
    if not err_class:
        err_class = "unknown"
    return (str(tool), str(err_class))


def is_repeated_failure(steps: Sequence[Any], threshold: int = 2) -> bool:
    """Ritorna True se gli ultimi `threshold` step hanno tutti la stessa
    signature `(tool, error_class)`. Default threshold=2: due fail
    consecutivi stesso (tool, error_class) = loop confermato.

    `steps` ammette qualsiasi sequence (lista o list+step corrente).
    Step OK interrompono la sequenza (reset).
    """
    if threshold < 2 or len(steps) < threshold:
        return False
    tail = list(steps)[-threshold:]
    sigs = [_step_signature(s) for s in tail]
    # Tutti devono essere fail (sig non None) E identici.
    first = sigs[0]
    if first is None:
        return False
    return all(s == first for s in sigs)


def repeated_failure_hint(steps: Sequence[Any]) -> str:
    """Compone hint user-facing dal pattern rilevato. Vuoto se non c'e'."""
    if not steps:
        return ""
    last = steps[-1]
    sig = _step_signature(last)
    if sig is None:
        return ""
    tool, err_class = sig
    from messages import get as _msg  # §11 i18n
    return _msg("MSG_LOOP_REPEATED_STEP", tool=tool, err_class=err_class)
