# SPDX-License-Identifier: AGPL-3.0-only
"""llm_pricing.py — FONTE UNICA delle tariffe LLM ($/Mtoken) per Metnos.

Consolida (15/6/2026) le tabelle prima duplicate e DIVERGENTI in
`executors/consult_frontier` e `runtime/llm_cost_sink` (entrambe avevano Opus a
$15/$75 — 3× il prezzo reale — e una riga fantasma `gpt-5`). Una sola tabella qui;
i consumatori importano `cost_usd`/`PRICING`. §7.2 (no duplicazione).

Prezzi: pagine pubbliche Anthropic, aggiornati 15/6/2026. Mancanze → costo 0.0
(degrade onesto: meglio 0 che un numero inventato). Aggiornare QUI quando cambiano.
"""
from __future__ import annotations

# (provider, model) -> (input $/Mtok, output $/Mtok)
PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-opus-4-8"):   (5.0, 25.0),
    ("anthropic", "claude-opus-4-7"):   (5.0, 25.0),
    ("anthropic", "claude-opus-4-6"):   (5.0, 25.0),
    ("anthropic", "claude-sonnet-4-6"): (3.0, 15.0),
    ("anthropic", "claude-haiku-4-5"):  (1.0, 5.0),
    ("anthropic", "claude-fable-5"):    (10.0, 50.0),
}


def price(provider: str | None, model: str | None) -> tuple[float, float]:
    """(input, output) $/Mtok per (provider, model); (0.0, 0.0) se ignoto."""
    return PRICING.get((provider or "", model or ""), (0.0, 0.0))


def cost_usd(provider: str | None, model: str | None,
             in_tok: int, out_tok: int) -> float:
    """Costo USD di una chiamata dati i token. 0.0 per modelli non tariffati."""
    in_p, out_p = price(provider, model)
    return round(in_tok * in_p / 1e6 + out_tok * out_p / 1e6, 6)
