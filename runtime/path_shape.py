"""path_shape.py — fingerprint deterministico della sequenza di executor.

Utilizzato dall'auto-evaluator delle proposte synth (ADR 0122). La "shape"
di un turno e' la sequenza ordinata dei `chosen_tool` produttivi (esclusi
`final_answer`, `describe_entries` puro, `undo_last_turn`, builtin meta
come `request_new_executor`/`scratchpad_read`/`@uploaded`). Stesso shape
hash = stesso percorso multi-step deterministico, indipendentemente dalla
formulazione naturale della query.

Determinismo §7.9: pure compute, niente LLM.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

# Tool-name esclusi dallo shape: non sono passi sostituibili da un nuovo
# executor (final_answer = chiusura turno, describe_entries = sintesi
# user-facing, undo = rollback, builtin meta = sintesi/scratchpad).
_SHAPE_EXCLUDED: frozenset[str] = frozenset({
    "final_answer",
    "describe_entries",
    "undo_last_turn",
    "request_new_executor",
    "scratchpad_read",
    "@uploaded",
})


def _normalize(tool: str | None) -> str | None:
    """Normalizza un nome di tool per il calcolo dello shape.

    Ritorna None per tool da escludere (final_answer, describe_entries, ...).
    Altrimenti torna il nome stringato.
    """
    if not tool:
        return None
    name = str(tool).strip()
    if not name or name in _SHAPE_EXCLUDED:
        return None
    return name


def _step_field(step: Any, key: str) -> Any:
    """Accede a `key` su `step` accettando sia dict sia oggetti con attributo
    (es. StepLog dataclass)."""
    if isinstance(step, dict):
        return step.get(key)
    return getattr(step, key, None)


def steps_to_tools(steps: Iterable[Any]) -> list[str]:
    """Estrae la sequenza di `chosen_tool` produttivi da una lista di step.

    Accetta sia dict (turn JSONL) sia dataclass-like (`StepLog` runtime).

    Filtra:
    - step privi di `chosen_tool`,
    - step in errore (`error` non None) — un fallimento non e' parte
      dello shape "happy path",
    - tool nel set escluso (final_answer, describe_entries, ...).
    """
    out: list[str] = []
    for s in steps or []:
        if s is None:
            continue
        if _step_field(s, "error"):
            continue
        name = _normalize(_step_field(s, "chosen_tool"))
        if name is None:
            continue
        out.append(name)
    return out


def path_shape_hash(steps: Iterable[Any]) -> str:
    """SHA-256(16 hex char) della sequenza di chosen_tool produttivi.

    Vuoto (nessun step utile) → stringa vuota: il caller decide come
    gestire (skip aggregation, ad esempio).
    """
    tools = steps_to_tools(steps)
    if not tools:
        return ""
    payload = "\n".join(tools).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def extract_path_shape(turn_record: dict) -> tuple[str, int]:
    """Ritorna `(shape_hash, n_steps)` per un record di turno JSONL.

    `n_steps` = numero di step produttivi (post-filtro). Hash = "" se
    nessuno step utile.
    """
    steps = (turn_record or {}).get("steps") or []
    tools = steps_to_tools(steps)
    if not tools:
        return ("", 0)
    payload = "\n".join(tools).encode("utf-8")
    return (hashlib.sha256(payload).hexdigest()[:16], len(tools))


def turn_total_ms(turn_record: dict) -> int | None:
    """Tempo totale wall-clock di un turno in millisecondi, oppure None.

    Preferisce `ts_end - ts_start` (gia' nel JSONL); fallback su somma
    `exec_ms` + `llm_latency_ms` per step se i timestamp mancano.
    """
    if not isinstance(turn_record, dict):
        return None
    t0 = turn_record.get("ts_start")
    t1 = turn_record.get("ts_end")
    if isinstance(t0, (int, float)) and isinstance(t1, (int, float)) and t1 > t0:
        return int((t1 - t0) * 1000)
    # Fallback: somma per-step.
    total = 0
    has_any = False
    for s in turn_record.get("steps") or []:
        if not isinstance(s, dict):
            continue
        for k in ("exec_ms", "llm_latency_ms", "intent_ms",
                  "vaglio_ms", "rerank_ms", "prefilter_ms"):
            v = s.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                total += int(v)
                has_any = True
    return total if has_any else None


def is_shape_terminal(steps: Iterable[Any]) -> bool:
    """True se l'ultimo step del turno e' `final_answer` (closure ok).

    Indica un turno arrivato a chiusura naturale, non interrotto da
    cap_steps / loop_break / errore. Usato dall'evaluator come signal
    di "pipeline_terminal".
    """
    last_tool: str | None = None
    for s in steps or []:
        if s is None:
            continue
        t = _step_field(s, "chosen_tool")
        if t:
            last_tool = str(t)
    return last_tool == "final_answer"


__all__ = [
    "path_shape_hash",
    "extract_path_shape",
    "steps_to_tools",
    "turn_total_ms",
    "is_shape_terminal",
]
