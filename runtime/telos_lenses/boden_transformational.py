# SPDX-License-Identifier: AGPL-3.0-only
"""boden_transformational.py — revisione contratto di un executor.

Boden 1990 trasformazionale: cambia signature args/schema/ritorno,
non solo default (M-SCAMPER) o adapt (A-SCAMPER).
"""
from __future__ import annotations

from ._base import (
    LensCtx, SHARED_PREAMBLE, SHARED_NAMING_SCHEMA,
    SHARED_OUTPUT_FORMAT, context_block,
)

NAME = "boden_transformational"
OPERATORS = ("nuovo_contratto",)


def build_prompt(ctx: LensCtx, operator: str) -> str:
    return f"""{SHARED_PREAMBLE}

TELOS: {ctx.telos.phrase}
Note utente: {ctx.telos.notes}

{context_block(ctx)}

OPERATORE: nuovo_contratto.
COSA FARE: scegli un executor dal catalog vivo. Proponi TRASFORMAZIONE
del contratto: nuovi args, schema ridisegnato, ritorno arricchito.
NB: cambia il CONTRATTO, non il VOCABOLARIO (il name resta o aggiunge
descriptor `_v2`/`_unified` SE il qualifier 3° e' presente).

Output: target = executor del catalog;
proposed_action: "CONTRATTO ATTUALE: <X> | NUOVO: <Y> | DELTA: <potenza>"

{SHARED_NAMING_SCHEMA}

{SHARED_OUTPUT_FORMAT}
"""
