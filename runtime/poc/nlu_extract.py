#!/usr/bin/env python3
"""POC (NON wired) — estrazione frase->JSON strutturato via LLM vincolato.

Obiettivo: dimostrare che un LLM piccolo+veloce (il Qwen 3.6 35B-A3B locale,
3B attivi) con OUTPUT VINCOLATO a JSON-schema puo' sostituire i regex
pre-cablati di estrazione strutturata (ordering/time_window/recurrence/...),
eliminando il problema lingua+manutenzione, restando deterministico (§7.9:
temp0 + seed) e con JSON SEMPRE valido per costruzione (constrained decoding).

Generico: il chiamante fornisce uno SCHEMA dichiarativo + istruzione; nessuna
logica per-dominio qui dentro. Non importa nulla del runtime Metnos: parla
direttamente col llama-server OpenAI-compat, cosi' e' isolato e misurabile.

NB: questo file e' un proof-of-concept fuori dal path di esecuzione; non
modifica ne' wira alcun modulo esistente.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

# Stesso server/modello della produzione (no hardcoding endpoint, §7.11).
_ENDPOINT = os.environ.get("METNOS_LLM_ENDPOINT", "http://localhost:8080")
_URL = _ENDPOINT.rstrip("/") + "/v1/chat/completions"
_SEED = int(os.environ.get("METNOS_LLM_SEED", "42"))


def extract(text: str, schema: dict, instruction: str, *,
            examples: list[tuple[str, dict]] | None = None,
            max_tokens: int = 256, timeout: float = 60.0) -> tuple[dict, dict]:
    """Estrae da `text` una struttura conforme a `schema` (JSON-schema).

    Ritorna (data, meta) dove meta = {latency_ms, raw, ok}. L'output e'
    vincolato dal server al JSON-schema => strutturalmente valido per
    costruzione. Deterministico: temperature=0 + seed fisso + think off.
    `examples`: few-shot opzionale (frase, json-atteso) per ancorare la
    semantica senza enumerare regole (sostituisce le tabelle regex).
    """
    msgs = [{"role": "system", "content": instruction}]
    for ex_text, ex_json in (examples or []):
        msgs.append({"role": "user", "content": ex_text})
        msgs.append({"role": "assistant",
                     "content": json.dumps(ex_json, ensure_ascii=False)})
    msgs.append({"role": "user", "content": text})
    payload = {
        "model": "local",
        "temperature": 0,
        "seed": _SEED,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "extract", "schema": schema, "strict": True},
        },
        "messages": msgs,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(_URL, data=data,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=timeout))
        ms = (time.time() - t0) * 1000.0
        raw = resp["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(raw)
            return parsed, {"latency_ms": ms, "raw": raw, "ok": True}
        except json.JSONDecodeError:
            return {}, {"latency_ms": ms, "raw": raw, "ok": False}
    except Exception as ex:
        ms = (time.time() - t0) * 1000.0
        return {}, {"latency_ms": ms, "raw": f"{type(ex).__name__}: {ex}",
                    "ok": False}


# ── Schema UNIFICATO Metnos: una sola call estrae TUTTI gli slot che oggi
#    richiedono N passate regex separate (ordering, time_window, recurrence,
#    count, visualize). E' il guadagno architetturale: 1 LLM call vs N regex.
METNOS_NLU_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ordering": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["none", "sort", "group"]},
                "key": {"type": "string"},
                "desc": {"type": "boolean"},
            },
            "required": ["mode", "key", "desc"],
        },
        "time_window": {
            "type": "string",
            "description": "'' | today | last-<N>d | last-<N>h | next-<N>d | "
                           "next-<N>h | YYYY-MM-DD..YYYY-MM-DD  (N from phrase)",
        },
        "recurrence": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "every": {"type": "string",
                          "description": "'' | <N>m | <N>h | <N>d | <N>w"},
                "at": {"type": "string", "description": "'' or 'HH:MM'"},
            },
            "required": ["every", "at"],
        },
        "count_intent": {"type": "boolean"},
        "visualize_intent": {"type": "boolean"},
    },
    "required": ["ordering", "time_window", "recurrence",
                 "count_intent", "visualize_intent"],
}

# Language-independent function → English prompt; imperative, terse,
# pattern-oriented. Input may be ANY language; the spec stays one (§ no
# per-language grammar). Trigger words are cross-lingual EXAMPLES, not rules.
METNOS_NLU_INSTRUCTION = (
    "Extract NLU slots from a user phrase in ANY language. Emit EXPLICIT "
    "slots only. When unsure, leave empty/none/false — precision over recall.\n"
    "ordering.mode: 'sort' ONLY on an explicit sort verb "
    "(ordina|sort|order|trie|ordena|sortieren); 'group' ONLY on an explicit "
    "group verb (raggruppa|group by|gruppiere|agrupa); else 'none'. Never "
    "infer from adjectives (new|recent|important). ordering.key: the field or "
    "''. ordering.desc: true only if descending is requested.\n"
    "time_window: ONLY on an explicit time reference; normalize to "
    "today|last-<N>d|last-<N>h|next-<N>d|next-<N>h|YYYY-MM-DD..YYYY-MM-DD, "
    "N = number from the phrase, keep past(last)/future(next) direction; "
    "else ''.\n"
    "recurrence.every: ONLY on explicit repetition, pattern <N>m|<N>h|<N>d|"
    "<N>w; else ''. recurrence.at: 'HH:MM' or ''.\n"
    "count_intent: true ONLY on an explicit quantity ask "
    "(quanti|how many|conta). visualize_intent: true ONLY on an explicit show "
    "verb (mostra|visualizza|show|display); never for list|read|summarize.\n"
    "Output JSON only. No prose. Invent nothing."
)


def metnos_nlu(text: str) -> tuple[dict, dict]:
    """Estrazione unificata Metnos (un solo round-trip)."""
    return extract(text, METNOS_NLU_SCHEMA, METNOS_NLU_INSTRUCTION,
                   max_tokens=200)


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "ordina le mail per data, le piu' recenti prima"
    data, meta = metnos_nlu(q)
    print(f"[{meta['latency_ms']:.0f}ms ok={meta['ok']}]")
    print(json.dumps(data, ensure_ascii=False, indent=2))
