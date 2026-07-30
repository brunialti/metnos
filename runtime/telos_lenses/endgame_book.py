# SPDX-License-Identifier: AGPL-3.0-only
"""endgame_book.py — precompute pattern di scadenze.

Per t.puntualita: cataloga pattern noti (input → cascata reminder)
deterministici per scheduler v2. Naming-aware: puo' proporre
nuovi nomi 4-livello per varianti di set_tasks_<qualifier>_<descriptor>.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_SCHEMA,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "endgame_book"
OPERATORS = ("pattern_scadenza",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE: pattern_scadenza.
COSA FARE: identifica un PATTERN-CON-DEADLINE ricorrente (fatture,
scadenze fiscali, certificati, appuntamenti) e descrivi la cascata
di reminder/azioni Metnos deterministiche (no LLM al fire-time).

Output: target = executor che intercetta l'input;
proposed_action: "PATTERN: INPUT=<tipo> | CASCATA: <azione@T0>, <azione@T-Ngg>, ..."

{SHARED_NAMING_SCHEMA}

{SHARED_OUTPUT_FORMAT}
"""
