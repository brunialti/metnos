# SPDX-License-Identifier: AGPL-3.0-only
"""analogy_transfer.py — trasferimento strutturale di strategie.

Identifica strategia di successo in un dominio A (uses elevato
mnestoma) e la trasferisce a dominio B strutturalmente simile.
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_SCHEMA,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "analogy_transfer"
OPERATORS = ("transfer_struttura",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE: transfer_struttura.
COSA FARE: identifica strategia gia' applicata in dominio A (credentials
Fernet, signatures hash chain, scheduler trigger grammar...) e
TRASFERISCILA a dominio B isomorfo.

Output: target = executor del dominio B (destinazione);
proposed_action: "ANALOGIA: <dominio A>:<strategia S> → <dominio B>:<applicazione>"

{SHARED_NAMING_SCHEMA}

{SHARED_OUTPUT_FORMAT}
"""
