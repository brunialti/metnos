# SPDX-License-Identifier: AGPL-3.0-only
"""oulipo.py — vincolo deliberato (OuLiPo).

Concept-only lens: propone vincoli temporanei, non variant di executor.
new_op_name resta null. Gemma puo' usare il campo executor_target solo
come riferimento a cosa il vincolo tocca.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_NULL,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "oulipo"
OPERATORS = ("vincolo_risorsa", "vincolo_tempo", "vincolo_executor")

_DESCRIPTIONS = {
    "vincolo_risorsa": "VINCOLO DI RISORSA temporaneo (no API frontier per N gg, no rete, no provider Google).",
    "vincolo_tempo": "VINCOLO TEMPORALE (es. nessun executor mutante prima delle 09:00, batch solo in finestra notturna).",
    "vincolo_executor": "VINCOLO DI ESECUTORE (es. no consult_frontier per N gg, no executor X).",
}


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE OULIPO: {operator}
{_DESCRIPTIONS[operator]}
NB: il vincolo riguarda METNOS (proposals/scheduler/fallback), non l'utente.

Genera 1-2 vincoli concreti (cosa, per quanto, su quale scope).

{SHARED_NAMING_NULL}

{SHARED_OUTPUT_FORMAT}
"""
