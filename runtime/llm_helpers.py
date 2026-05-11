"""runtime.llm_helpers — API minimale per executor LLM-augmented.

Pattern terza categoria di executor (28/4/2026, vedi
`feedback_llm_augmented_executors`): un executor riceve dati + un
prompt, dentro chiama un LLM, ritorna testo. Per non duplicare logica
di routing in ogni nuovo executor, esponiamo qui la funzione minima:

    from llm_helpers import call_llm
    text, meta = call_llm(query, prompt, tier='middle', max_tokens=600)

`query` puo' essere stringa, dict, lista (verra' serializzata in JSON
compatto) o gia' una stringa formattata.

`prompt` e' il system prompt: il mestiere semantico del chiamante (es.
"sintetizza per importanza", "traduci in inglese", "estrai entita'").

`tier` punta al LLM router runtime. Tier disponibili: 'fast', 'middle',
'wise'. Default 'middle' per task di sintesi/classificazione/scrittura
breve. Per ora hardcoded llamacpp Gemma 4 26B per tutti e tre i tier
finche' il LLMRouter ufficiale non e' agganciato.

Capability implicita: `llm:call` (l'executor che usa questo helper
deve dichiararla nel manifest, quando il loader le fara' rispettare).
"""
from __future__ import annotations

import json
import time
from typing import Any

from llm_provider import LlamaCppProvider

LLAMA_ENDPOINT = "http://127.0.0.1:8080"
TIER_MODELS = {
    # Hardcoded oggi; quando arriva LLMRouter (memoria
    # `metnos_design_3tier_llm`) ogni tier punta a provider diversi.
    "fast": "gemma-4-26B",
    "middle": "gemma-4-26B",
    "wise": "gemma-4-26B",
}


def _serialize_query(q: Any, max_chars: int = 12000) -> str:
    if isinstance(q, str):
        return q if len(q) <= max_chars else q[:max_chars] + "\n... [truncated]"
    txt = json.dumps(q, ensure_ascii=False)
    if len(txt) <= max_chars:
        return txt
    return txt[:max_chars] + "\n... [truncated]"


def call_llm(
    query: Any,
    prompt: str,
    *,
    tier: str = "middle",
    max_tokens: int = 600,
    temperature: float = 0.0,
    think: bool = False,
) -> tuple[str, dict]:
    """Chiama il LLM del tier indicato. Ritorna (text, meta).

    Solleva eccezione se il provider non e' raggiungibile o l'LLM
    risponde vuoto. L'executor chiamante deve gestirla e tradurla in
    una observation `{ok: false, error_code: ERR_EXT_SVC_UNAVAILABLE}`.
    """
    if tier not in TIER_MODELS:
        raise ValueError(f"unknown tier {tier!r}; valid: {list(TIER_MODELS)}")
    model = TIER_MODELS[tier]
    # ADR 0120: slot affinity. Default Metnos = id_slot=1 (image enrichment
    # batch). Override via env var METNOS_LLM_SLOT_ID. None disabilita.
    import os as _os
    _slot_env = _os.environ.get("METNOS_LLM_SLOT_ID", "1").strip()
    _slot = int(_slot_env) if _slot_env.isdigit() else None
    provider = LlamaCppProvider(model=model, endpoint=LLAMA_ENDPOINT, id_slot=_slot)
    user_payload = _serialize_query(query)
    t0 = time.time()
    r = provider.chat(prompt, user_payload, max_tokens=max_tokens,
                      temperature=temperature, think=think)
    latency_ms = int((time.time() - t0) * 1000)
    text = (r.text or "").strip()
    meta = {
        "tier": tier,
        "model": model,
        "in_tokens": r.in_tokens,
        "out_tokens": r.out_tokens,
        "latency_ms": latency_ms,
    }
    return text, meta
