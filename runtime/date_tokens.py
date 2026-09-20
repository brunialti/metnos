# SPDX-License-Identifier: AGPL-3.0-only
"""date_tokens.py — sostituzione deterministica dei token-data nei prompt.

Universale (§7.3) + deterministico (§7.9): risolve i placeholder data nei
TESTI di prompt che NON passano da Jinja — le sezioni planner `.yaml` rese in
prosa (`prompt_loader._render_yaml_section`) e le `description` dei manifest
`.toml` lette dal Proposer (`engine/proposer._render_tool_pool`).

Stessa convenzione dei `.j2` (`{{ current_year }}` / `{{ current_date }}`,
vedi `prompt_loader._default_vars`): gli ESEMPI nei prompt non hardcodano anni
letterali che invecchiano e biasano l'LLM verso anni stantii (§7.11-per-le-date).
Cosi' un autore usa LA STESSA sintassi indipendentemente dal tipo di file, e i
manifest restano su disco col token LETTERALE (nessun re-sign §7.10: la
risoluzione avviene al render, non sul file firmato).

Tollerante allo spazio interno: `{{current_year}}` == `{{ current_year }}`.
Token sconosciuti restano invariati (no corruption silenziosa §2.8).
"""
from __future__ import annotations

import re
from datetime import datetime

# {{ token }} con spazi opzionali. Solo i token-data NOTI sono sostituiti;
# qualsiasi altro `{{ ... }}` resta intatto.
_TOKEN_RE = re.compile(r"\{\{\s*(current_year|current_date|current_month)\s*\}\}")


def substitute_date_tokens(text: str, *, now: datetime | None = None) -> str:
    """Sostituisce i token-data noti nel testo. Pure-compute; un anno
    letterale NON e' un token quindi resta invariato (idempotente)."""
    if not text or "{{" not in text:
        return text
    n = now or datetime.now()
    values = {
        "current_year": str(n.year),
        "current_date": n.strftime("%Y-%m-%d"),
        "current_month": f"{n.month:02d}",
    }
    return _TOKEN_RE.sub(lambda m: values[m.group(1)], text)
