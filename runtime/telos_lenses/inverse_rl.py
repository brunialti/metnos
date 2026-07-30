# SPDX-License-Identifier: AGPL-3.0-only
"""inverse_rl.py — discover unstated telos.

Concept-only lens: propone TELOS non dichiarati osservando cluster di
turni soddisfatti (no follow-up correttivo). Non variant di executor.
new_op_name resta null.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_NULL,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "inverse_rl"
OPERATORS = ("cluster_soddisfatto",)

_DECLARED_TELOS = """TELOS GIA' DICHIARATI (da NON riproporre):
- t.tempo, t.ordine, t.puntualita, t.protezione,
- t.discrezione, t.parsimonia, t.coltivazione_strumenti."""


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS DI RIFERIMENTO: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{_DECLARED_TELOS}

{context_block(ctx)}

OPERATORE: cluster_soddisfatto.
COSA FARE: osserva mnestoma + patterns. Individua un FILO ricorrente
NON coperto dai 7 telos sopra. Esprimi come PROPOSED_TELOS in forma
imperativa: "Mantenere/Liberare/Garantire/Coltivare <X>".

Output: target_executor = il piu' rappresentativo del cluster,
proposed_action inizia con "PROPOSED_TELOS: ...".

{SHARED_NAMING_NULL}

{SHARED_OUTPUT_FORMAT}
"""
